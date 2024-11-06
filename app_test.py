from flask import Flask, render_template, request
import openrouteservice
from openrouteservice import convert
import folium
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
import requests  # Pour appeler l'API des Bornes de Recharge
import math  # Pour calculer les distances avec la formule de Haversine

app = Flask(__name__)
# @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
# @@@@@@@@@@@@@@@VRAI CODE@@@@@@@@@@@@@@@@@@@@@@@
# @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@ 

# Remplacez ces variables par vos propres clés API
CHARGETRIP_API_KEY = '670f7296021ae87118926adb'
CHARGETRIP_APP_ID = '670f7296021ae87118926add'
OPENROUTESERVICE_API_KEY = '5b3ce3597851110001cf624844e454ed2cda41739d0e71bbc67de648'

def haversine_distance(coord1, coord2):
    """
    Calcule la distance entre deux points géographiques en utilisant la formule de Haversine.
    Coordonnées au format [longitude, latitude].
    Retourne la distance en mètres.
    """
    lon1, lat1 = coord1
    lon2, lat2 = coord2
    R = 6371000  # Rayon de la Terre en mètres
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    meters = R * c
    return meters

def get_coordinates(place_name, client):
    """
    Utilise l'API de géocodage d'OpenRouteService pour obtenir les coordonnées GPS d'un lieu.
    """
    try:
        geocode = client.pelias_search(text=place_name)
        if geocode['features']:
            coordinates = geocode['features'][0]['geometry']['coordinates']
            # Les coordonnées sont au format [longitude, latitude]
            return coordinates
        else:
            print(f"Le lieu '{place_name}' n'a pas été trouvé.")
            return None
    except Exception as e:
        print(f"Erreur lors de la récupération des coordonnées pour '{place_name}': {e}")
        return None

def get_vehicle_list():
    """
    Interroge l'API GraphQL de Chargetrip pour obtenir la liste des véhicules électriques.
    """
    transport = RequestsHTTPTransport(
        url='https://api.chargetrip.io/graphql',
        headers = {
            "Content-Type": "application/json",
            "x-client-id": CHARGETRIP_API_KEY,
            "x-app-id": CHARGETRIP_APP_ID
        },
        use_json=True,
    )

    client = Client(transport=transport, fetch_schema_from_transport=True)

    requete = gql('''
        query vehicleList($page: Int, $size: Int, $search: String) {
            carList(
                page: $page, 
                size: $size, 
                search: $search
            ) {
                id
                naming {
                    make
                    model
                    chargetrip_version
                }
                media {
                    image {
                        thumbnail_url
                    }
                }
                range {
                    chargetrip_range {
                        best
                        worst
                    }
                }
            }
        }
    ''')

    try:
        result = client.execute(requete)
        vehicules = result.get('carList', [])
        if not vehicules:
            print("Aucun véhicule trouvé dans la réponse.")
        return vehicules
    except Exception as e:
        print(f"Erreur lors de la récupération des véhicules : {e}")
        return []

def get_nearest_charging_station(coord):
    """
    Trouve la borne de recharge la plus proche d'une coordonnée donnée en utilisant l'API des Bornes de recharge.
    """
    # Construire l'URL pour l'API
    url = 'https://opendata.reseaux-energies.fr/api/records/1.0/search/'
    params = {
        'dataset': 'bornes-irve',
        'geofilter.distance': f"{coord[1]},{coord[0]},50000",  # Rayon de 50 km
        'rows': 10  # Récupérer plusieurs bornes pour choisir la plus proche
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()
        if data['nhits'] > 0:
            # Trouver la borne la plus proche
            min_distance = float('inf')
            nearest_station = None
            for record in data['records']:
                station_lon = record['fields'].get('xlongitude')
                station_lat = record['fields'].get('ylatitude')
                if station_lon is not None and station_lat is not None:
                    station_coord = [station_lon, station_lat]
                    distance = haversine_distance(coord, station_coord)
                    if distance < min_distance:
                        min_distance = distance
                        nearest_station = station_coord
            if nearest_station:
                return nearest_station
            else:
                print("Aucune borne de recharge valide trouvée à proximité.")
                return None
        else:
            print("Aucune borne de recharge trouvée à proximité.")
            return None
    except Exception as e:
        print(f"Erreur lors de la récupération de la borne de recharge : {e}")
        return None

@app.route('/', methods=['GET', 'POST'])
def index():
    # Obtenir la liste des véhicules
    vehicules = get_vehicle_list()

    if request.method == 'POST':
        ville_depart = request.form['ville_depart']
        ville_arrivee = request.form['ville_arrivee']
        vehicule_id = request.form['vehicule']

        # Trouver le véhicule sélectionné
        vehicule_selectionne = next((v for v in vehicules if v['id'] == vehicule_id), None)

        if vehicule_selectionne is None:
            erreur = "Véhicule non trouvé. Veuillez réessayer."
            return render_template('index.html', erreur=erreur, vehicules=vehicules)

        # Obtenir les caractéristiques du véhicule
        autonomie_best = vehicule_selectionne['range']['chargetrip_range']['best']
        autonomie_worst = vehicule_selectionne['range']['chargetrip_range']['worst']
        autonomie_moyenne = (autonomie_best + autonomie_worst) / 2  # en kilomètres

        # Créer le client OpenRouteService avec la clé API
        client = openrouteservice.Client(key=OPENROUTESERVICE_API_KEY)

        coord_depart = get_coordinates(ville_depart, client)
        coord_arrivee = get_coordinates(ville_arrivee, client)

        if coord_depart and coord_arrivee:
            # Construire la liste des coordonnées pour l'API directions
            coords = [coord_depart, coord_arrivee]

            # Obtenir l'itinéraire initial
            route = client.directions(coords, format='geojson')
            geometry = route['features'][0]['geometry']
            distance_m = route['features'][0]['properties']['summary']['distance']
            distance_km = distance_m / 1000

            # Décoder la géométrie pour obtenir les points du trajet
            route_coords = geometry['coordinates']

            # Calculer les distances cumulées le long du trajet
            distances = [0]
            for i in range(1, len(route_coords)):
                segment_distance = haversine_distance(route_coords[i-1], route_coords[i])
                distances.append(distances[-1] + segment_distance)

            # Trouver les points où l'autonomie est atteinte
            autonomie_m = autonomie_moyenne * 1000  # Convertir en mètres
            points_arrets = []
            last_stop_distance = 0

            for i, dist in enumerate(distances):
                if dist - last_stop_distance >= autonomie_m and len(points_arrets) < (distance_m // autonomie_m):
                    coord = route_coords[i]
                    station_coord = get_nearest_charging_station(coord)
                    if station_coord:
                        points_arrets.append({
                            'coord': [station_coord[1], station_coord[0]],  # [lat, lon]
                            'original_coord': coord
                        })
                        last_stop_distance = dist

            # Recalculer l'itinéraire en incluant les bornes de recharge comme waypoints
            waypoints = [coord_depart] + [p['coord'][::-1] for p in points_arrets] + [coord_arrivee]
            new_route = client.directions(waypoints, format='geojson')

            # Mettre à jour la géométrie et les distances
            new_geometry = new_route['features'][0]['geometry']
            new_distance_m = new_route['features'][0]['properties']['summary']['distance']
            new_distance_km = new_distance_m / 1000

            # Créer une carte centrée sur le point de départ
            m = folium.Map(location=[coord_depart[1], coord_depart[0]], zoom_start=6)

            # Ajouter l'itinéraire à la carte
            folium.GeoJson(new_geometry, name='Itinéraire').add_to(m)

            # Ajouter des marqueurs pour les points de départ et d'arrivée
            folium.Marker(
                location=[coord_depart[1], coord_depart[0]],
                popup=ville_depart,
                icon=folium.Icon(color='green')
            ).add_to(m)

            folium.Marker(
                location=[coord_arrivee[1], coord_arrivee[0]],
                popup=ville_arrivee,
                icon=folium.Icon(color='red')
            ).add_to(m)

            # Ajouter des marqueurs pour les bornes de recharge
            for idx, arret in enumerate(points_arrets):
                folium.Marker(
                    location=[arret['coord'][0], arret['coord'][1]],
                    popup=f"Borne de recharge {idx+1}",
                    icon=folium.Icon(color='blue', icon='bolt', prefix='fa')
                ).add_to(m)

            # Générer le HTML de la carte
            map_html = m._repr_html_()

            # Calcul du nombre d'arrêts
            nombre_arrets = len(points_arrets)

            # Rendre 'index.html' en passant les données
            return render_template(
                'index.html',
                map_html=map_html,
                distance=round(new_distance_km, 2),
                vehicules=vehicules,
                nombre_arrets=nombre_arrets
            )
        else:
            erreur = "L'une des villes n'a pas été trouvée. Veuillez réessayer."
            return render_template('index.html', erreur=erreur, vehicules=vehicules)
    else:
        return render_template('index.html', vehicules=vehicules)

if __name__ == '__main__':
    app.run(debug=True)