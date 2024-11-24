import os
import json
import logging
from datetime import timedelta, datetime
import re

from utils.utils import create_requests_session

class BeatportApi:
    def __init__(self):
        self.API_URL = 'https://api.beatport.com/v4/'
        self.AUTH_URL = self.API_URL
        
        # Mobile app client details
        self.client_id = 'ryZ8LuyQVPqbK2mBX2Hwt4qSMtnWuTYSqBPO92yQ'
        
        self.access_token = None
        self.refresh_token = None 
        self.expires = None
        
        self.s = create_requests_session()
        
        # Setup debug logging
        debug_dir = 'debug'
        if not os.path.exists(debug_dir):
            os.makedirs(debug_dir)
        
        self.debug_log = logging.getLogger('beatport_debug')
        self.debug_log.setLevel(logging.DEBUG)
        self.debug_log.propagate = False  # Prevent duplicate logging
        
        # Only add handler if it doesn't exist
        if not self.debug_log.handlers:
            # Add file handler for debug logging
            fh = logging.FileHandler(os.path.join(debug_dir, 'beatport_auth_debug.log'))
            fh.setLevel(logging.DEBUG)
            formatter = logging.Formatter('%(asctime)s - %(message)s')
            fh.setFormatter(formatter)
            self.debug_log.addHandler(fh)

        # Initialize debug mode as disabled
        self.debug_enabled = False

    def headers(self, use_access_token: bool = False):
        """Get consistent headers for all requests"""
        headers = {
            'Accept-Encoding': 'gzip',
            'Connection': 'Keep-Alive',
            'User-Agent': 'Mozilla/5.0'  # Use simpler User-Agent
        }
        if use_access_token:
            headers['Authorization'] = f'Bearer {self.access_token}'
        return headers

    def _sanitize_data(self, data):
        """Sanitize sensitive data from logs"""
        if not data:
            return data
        
        # Convert to dict if string
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except:
                return data
            
        # Make a copy to avoid modifying original
        if isinstance(data, dict):
            data = data.copy()
            
            # List of fields to sanitize
            sensitive_fields = {
                'username': '***',
                'password': '***',
                'email': '***@***.***',
                'firstName': '***',
                'lastName': '***',
                'phone_number': '***',
                'phone_primary': '***',
                'address1': '***',
                'address2': '***',
                'city': '***',
                'zip': '***',
                'first_name': '***',
                'last_name': '***',
                'name': '***',  # Only if in user/account context
                'card_type': '***',
                'last_four': '***'
            }
            
            # Sanitize fields
            for field, mask in sensitive_fields.items():
                if field in data:
                    data[field] = mask
            
            # Handle nested objects
            for key, value in data.items():
                if isinstance(value, dict):
                    data[key] = self._sanitize_data(value)
                elif isinstance(value, list):
                    data[key] = [self._sanitize_data(item) if isinstance(item, dict) else item for item in value]
            
        return data

    def _log_request_response(self, method, url, headers, data=None, response=None):
        """Log request and response details"""
        if not self.debug_enabled:
            return
        
        self.debug_log.debug(f"\nREQUEST:")
        self.debug_log.debug(f"METHOD: {method}")
        self.debug_log.debug(f"URL: {url}")
        self.debug_log.debug("HEADERS:")
        
        # Copy and sanitize headers
        headers_copy = headers.copy() if headers else {}
        if 'Authorization' in headers_copy:
            # Keep first 50 chars of bearer token
            auth = headers_copy['Authorization']
            if auth.startswith('Bearer '):
                headers_copy['Authorization'] = auth[:50] + '...'
            
        for k, v in headers_copy.items():
            self.debug_log.debug(f"{k}: {v}")
        
        if data:
            self.debug_log.debug("\nREQUEST BODY:")
            try:
                sanitized_data = self._sanitize_data(data)
                self.debug_log.debug(json.dumps(sanitized_data, indent=2))
            except:
                self.debug_log.debug(str(data))

        if response:
            self.debug_log.debug("\nRESPONSE:")
            self.debug_log.debug(f"STATUS: {response.status_code}")
            self.debug_log.debug("HEADERS:")
            for k, v in response.headers.items():
                self.debug_log.debug(f"{k}: {v}")
            self.debug_log.debug("\nRESPONSE BODY:")
            try:
                response_data = response.json()
                sanitized_response = self._sanitize_data(response_data)
                self.debug_log.debug(json.dumps(sanitized_response, indent=2))
            except:
                self.debug_log.debug(response.text)

    def auth(self, username: str, password: str) -> dict:
        """Web authentication flow"""
        # Step 1: Login with credentials
        login_url = f'{self.API_URL}auth/login/'
        login_data = {
            'username': username,
            'password': password
        }
        
        if self.debug_enabled:
            self.debug_log.debug(f"\nLogin Request:")
            self.debug_log.debug(f"URL: {login_url}")
            self.debug_log.debug(f"Data: {login_data}")
        
        r = self.s.post(login_url, json=login_data)
        
        if r.status_code != 200:
            raise Exception("Login failed - Invalid credentials")
        
        # Get session ID from cookies
        session_id = r.cookies['sessionid']  # Access cookie directly
        
        # Step 2: Get authorization code
        auth_url = f'{self.API_URL}auth/o/authorize/'
        auth_params = {
            'client_id': 'ryZ8LuyQVPqbK2mBX2Hwt4qSMtnWuTYSqBPO92yQ',
            'response_type': 'code'
        }
        auth_headers = {
            'Cookie': f'sessionid={session_id}'
        }
        
        if self.debug_enabled:
            self.debug_log.debug(f"\nAuth Request:")
            self.debug_log.debug(f"URL: {auth_url}")
            self.debug_log.debug(f"Headers: {auth_headers}")
        
        r = self.s.get(auth_url, params=auth_params, headers=auth_headers, allow_redirects=False)
        
        if r.status_code != 302:
            raise Exception("Authorization failed")
        
        location = r.headers.get('Location', '')
        if 'code=' not in location:
            raise Exception("No authorization code found")
        
        code = location.split('code=')[1].split('&')[0]
        
        # Step 3: Exchange code for tokens
        token_url = f'{self.API_URL}auth/o/token/'
        token_data = {
            'client_id': 'ryZ8LuyQVPqbK2mBX2Hwt4qSMtnWuTYSqBPO92yQ',
            'grant_type': 'authorization_code',
            'code': code
        }
        
        if self.debug_enabled:
            self.debug_log.debug(f"\nToken Request:")
            self.debug_log.debug(f"URL: {token_url}")
            self.debug_log.debug(f"Data: {token_data}")
        
        r = self.s.post(token_url, data=token_data)
        
        if r.status_code != 200:
            raise Exception("Token exchange failed")
        
        data = r.json()
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        self.expires = datetime.now() + timedelta(seconds=data['expires_in'])
        
        return data

    def refresh(self):
        """Refresh access token"""
        r = self.s.post(f'{self.API_URL}auth/o/token/',
            data={
                'client_id': self.client_id,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token'
            },
            headers={
                'Accept-Encoding': 'gzip',
                'Connection': 'Keep-Alive',
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'okhttp/4.12.0'
            })

        if r.status_code != 200:
            return r.json()

        data = r.json()
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        self.expires = datetime.now() + timedelta(seconds=data['expires_in'])
        return data

    def set_session(self, session: dict):
        self.access_token = session.get('access_token')
        self.refresh_token = session.get('refresh_token')
        self.expires = session.get('expires')

    def get_session(self):
        return {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expires': self.expires
        }

    def _get(self, endpoint: str, params: dict = None):
        """Add logging to GET requests"""
        if not params:
            params = {}
            
        url = f'{self.API_URL}{endpoint}'
        headers = self.headers(use_access_token=True)
        
        self._log_request_response('GET', url, headers)
        
        r = self.s.get(url, params=params, headers=headers)
        
        self._log_request_response('GET', url, headers, response=r)

        if r.status_code == 401:
            raise ValueError(r.text)

        if r.status_code not in {200, 201, 202}:
            raise ConnectionError(r.text)

        return r.json()

    def get_account(self):
        return self._get('auth/o/introspect')

    def get_track(self, track_id: str):
        return self._get(f'catalog/tracks/{track_id}')

    def get_release(self, release_id: str):
        return self._get(f'catalog/releases/{release_id}')

    def get_release_tracks(self, release_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/releases/{release_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_playlist(self, playlist_id: str):
        return self._get(f'catalog/playlists/{playlist_id}')

    def get_playlist_tracks(self, playlist_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/playlists/{playlist_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_chart(self, chart_id: str):
        return self._get(f'catalog/charts/{chart_id}')

    def get_chart_tracks(self, chart_id: str, page: int = 1, per_page: int = 100):
        """Get tracks from a chart, including genre-based charts"""
        # Check if it's a genre chart ID
        genre_match = re.match(r"genre-(\d+)-hype-(\d+)", chart_id)
        if genre_match:
            genre_id = genre_match.group(1)
            chart_type = genre_match.group(2)
            # Use genre chart endpoint
            endpoint = f'catalog/genres/{genre_id}/hype/{chart_type}/tracks'
        else:
            # Regular chart endpoint
            endpoint = f'catalog/charts/{chart_id}/tracks'
            
        return self._get(endpoint, params={
            'page': page,
            'per_page': per_page
        })

    def get_artist(self, artist_id: str):
        return self._get(f'catalog/artists/{artist_id}')

    def get_artist_tracks(self, artist_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/artists/{artist_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_label(self, label_id: str):
        return self._get(f'catalog/labels/{label_id}')

    def get_label_releases(self, label_id: str):
        return self._get(f'catalog/labels/{label_id}/releases')

    def get_search(self, query: str):
        return self._get('catalog/search', params={'q': query})

    def get_track_download(self, track_id: str, quality: str = 'high'):
        """Get download URL for a track"""
        url = f"{self.API_URL}catalog/tracks/{track_id}/download/"
        
        # Map quality to API parameter
        quality_map = {
            'low': '128k.aac',
            'medium': '128k.aac',
            'high': '256k.aac',
            'flac': 'lossless'  # This is the key difference!
        }
        
        params = {
            'quality': quality_map[quality]
        }
        
        headers = self.headers(use_access_token=True)
        
        # Log the request details
        if self.debug_enabled:
            self.debug_log.debug(f"\nDownload Request:")
            self.debug_log.debug(f"URL: {url}")
            self.debug_log.debug(f"Quality: {quality}")
            self.debug_log.debug(f"Params: {params}")
            self.debug_log.debug(f"Headers: {headers}")
        
        response = self.s.get(url, params=params, headers=headers)
        
        # Log the response
        if self.debug_enabled:
            self.debug_log.debug(f"\nDownload Response:")
            self.debug_log.debug(f"Status: {response.status_code}")
            self.debug_log.debug(f"Headers: {dict(response.headers)}")
            self.debug_log.debug(f"Body: {response.text}")
        
        if response.status_code != 200:
            error_msg = f"Failed to get download URL: {response.text}"
            if response.status_code == 404:
                error_msg = f"Track {track_id} not available for download"
            raise Exception(error_msg)
        
        data = response.json()
        
        # Add quality info
        QUALITY_MAPPING = {
            'low': {
                'display': 'AAC 128kbps',
                'extension': 'm4a'
            },
            'medium': {
                'display': 'AAC 128kbps',
                'extension': 'm4a'
            },
            'high': {
                'display': 'AAC 256kbps',
                'extension': 'm4a'
            },
            'flac': {
                'display': 'FLAC',
                'extension': 'flac'
            }
        }
        quality_info = QUALITY_MAPPING[quality]
        
        if self.debug_enabled:
            self.debug_log.debug(f"\nDownload URL: {data.get('location')}")
            
        data['download_url'] = data.get('location')  # The download URL is in 'location'
        data['quality_info'] = quality_info
        
        return data

    def get_subscription(self):
        """Get user's subscription status"""
        return self._get('auth/o/introspect')

    def get_library_playlist(self, playlist_id: str):
        """Get user's library playlist"""
        return self._get(f'my/playlists/{playlist_id}')

    def get_library_playlist_tracks(self, playlist_id: str, page: int = 1, per_page: int = 100):
        """Get tracks from user's library playlist"""
        return self._get(f'my/playlists/{playlist_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })