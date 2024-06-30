import requests
import base64
import os
import csv
import time
import urllib.parse
from tqdm import tqdm
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()

client_id = os.getenv('SPOTIFY_CLIENT_ID')
client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')

options = Options()
options.headless = True
options.add_argument("--disable-popup-blocking")
options.add_argument("--disable-notifications")

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
initial_window = driver.current_window_handle

def get_access_token(client_id, client_secret):
    auth_url = "https://accounts.spotify.com/api/token"
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    headers = {"Authorization": f"Basic {encoded_credentials}"}
    data = {"grant_type": "client_credentials"}
    
    response = requests.post(auth_url, headers=headers, data=data)
    response.raise_for_status()
    return response.json()['access_token']

def get_playlist_tracks(access_token, playlist_id, max_retries=3):
    playlist_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    headers = {"Authorization": f"Bearer {access_token}"}
    tracks = []
    params = {"limit": 100, "offset": 0}
    retries = 0

    while retries < max_retries:
        try:
            with requests.Session() as session:
                response = session.get(playlist_url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                total_tracks = data['total']
                print(f"Total tracks in playlist: {total_tracks}")

                with tqdm(total=total_tracks, desc='Fetching tracks') as pbar:
                    while True:
                        items = data.get('items', [])
                        tracks.extend(items)
                        pbar.update(len(items))
                        
                        if not data['next']:
                            break

                        params['offset'] += params['limit']
                        time.sleep(2)
                        response = session.get(data['next'], headers=headers)
                        response.raise_for_status()
                        data = response.json()
            break
        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
            if response.status_code in {500, 502, 503, 504}:
                retries += 1
                wait_time = 2 ** retries
                print(f"Retrying in {wait_time} seconds... (Attempt {retries}/{max_retries})")
                time.sleep(wait_time)
                continue
            elif response.status_code == 401:
                print("Access token expired. Refreshing token...")
                access_token = get_access_token(client_id, client_secret)
                headers["Authorization"] = f"Bearer {access_token}"
            else:
                break
        except Exception as err:
            print(f"Other error occurred: {err}")
            break
    else:
        print("Max retries reached. Exiting.")

    return tracks

def has_alphabets(s):
    return any(c.isalpha() for c in s)

def fetch_youtube_link(title, artist):
    search_query = f"{title} {artist} official audio"
    google_url = f"https://www.google.com/search?q={urllib.parse.quote(search_query)}"
    
    try:
        page = requests.get(google_url)
        page.raise_for_status()
        soup = BeautifulSoup(page.content, 'html.parser')
        
        links = soup.find_all('a', href=True)
        youtube_link = None
        
        for link in links:
            href = link['href']
            if 'youtube.com/watch' in href or 'youtu.be/' in href:
                parsed_url = urllib.parse.urlparse(href)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                if 'q' in query_params:
                    youtube_link = query_params['q'][0]
                elif 'url' in query_params:
                    youtube_link = query_params['url'][0]
                else:
                    youtube_link = href
                break
        
        return youtube_link
    
    except Exception as e:
        print(f"Error fetching YouTube link for '{title}' by '{artist}': {e}")
    
    return None

def update_csv_with_youtube_links(csv_file):
    updated_rows = []
    
    with open(csv_file, mode='r', newline='', encoding='utf-8') as infile:
        reader = csv.reader(infile)
        header = next(reader)
        updated_rows.append(header + ['YouTube Link'])
        
        for row in tqdm(reader, desc="Fetching YouTube links"):
            title, artist = row[0], row[1]
            youtube_link = fetch_youtube_link(title, artist)
            updated_rows.append(row + [youtube_link if youtube_link else ""])
            print(f"Processed '{title}' by '{artist}'. YouTube link: {youtube_link if youtube_link else 'Not found'}")
    
    with open(csv_file, mode='w', newline='', encoding='utf-8') as outfile:
        writer = csv.writer(outfile)
        writer.writerows(updated_rows)

def refresh_browser_on_error(timeout=50):
    start_time = time.time()
    error_message = "An backend error occurred. Error code (p:3 / e:0)."
    
    while time.time() - start_time < timeout:
        try:
            error_div = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, f"//div[contains(text(), '{error_message}')]"))
            )
            print("Encountered backend error. Refreshing the page...")
            driver.refresh()
            time.sleep(3)
        except TimeoutException:
            print("No backend error found.")
            break
        except Exception as e:
            print(f"Error refreshing browser: {e}")

def download_youtube_mp3(youtube_url):
    try:
        driver.get('https://ytmp3s.nu/R6qp/')

        input_field = driver.find_element(By.ID, 'url')
        input_field.send_keys(youtube_url)

        convert_button = driver.find_element(By.CSS_SELECTOR, 'input[type="submit"][value="Convert"]')
        convert_button.click()

        refresh_browser_on_error()

        download_link = WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.LINK_TEXT, 'Download'))
        )
        download_link.click()

        handles = driver.window_handles
        if len(handles) > 1:
            for handle in handles[1:]:
                driver.switch_to.window(handle)
                driver.close()
        
        driver.switch_to.window(handles[0])

        time.sleep(6)

    except Exception as e:
        print(f"An error occurred while processing {youtube_url}: {e}")

def save_tracks_to_csv(tracks, filename='playlist_tracks.csv'):
    with open(filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['Track Name', 'Artists'])
        for item in tracks:
            track = item.get('track')
            if not track:
                continue
            
            track_name = track.get('name')
            if not isinstance(track_name, str):
                continue
            
            artists = ', '.join(artist['name'] for artist in track['artists'] if isinstance(artist.get('name'), str))
            if not has_alphabets(track_name) or not has_alphabets(artists):
                continue
            
            writer.writerow([track_name, artists])

def main():
    access_token = get_access_token(client_id, client_secret)

    playlist_1_id = 'Spotify playlist ID'

    tracks = get_playlist_tracks(access_token, playlist_1_id)

    csv_file = 'playlist_1_tracks.csv'

    save_tracks_to_csv(tracks, csv_file)
    update_csv_with_youtube_links(csv_file)

    with open(csv_file, mode='r', newline='', encoding='utf-8') as infile:
        reader = csv.DictReader(infile)
        
        for row in reader:
            youtube_url = row['YouTube Link']
            if youtube_url:
                print(f"Downloading MP3 for: {row['Track Name']} by {row['Artists']} - {youtube_url}")
                download_youtube_mp3(youtube_url)
            else:
                print(f"No YouTube URL found for: {row['Track Name']} by {row['Artists']}. Skipping download.")

if __name__ == "__main__":
    main()
