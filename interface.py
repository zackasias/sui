import logging
import re
import shutil
import ffmpeg
from datetime import datetime

from utils.models import *
from utils.utils import create_temp_filename
from .beatport_api import BeatportApi

module_information = ModuleInformation(
    service_name='Beatport',
    module_supported_modes=ModuleModes.download | ModuleModes.covers,
    session_settings={
        'username': '',
        'password': '',
        'debug': bool
    },
    session_storage_variables=['access_token', 'refresh_token', 'expires'],
    netlocation_constant='beatport',
    url_decoding=ManualEnum.manual,
    test_url='https://www.beatport.com/track/darkside/10844269'
)

class ModuleInterface:
    # noinspection PyTypeChecker
    def __init__(self, module_controller: ModuleController):
        self.exception = module_controller.module_error
        self.disable_subscription_check = module_controller.orpheus_options.disable_subscription_check
        self.oprinter = module_controller.printer_controller
        self.print = module_controller.printer_controller.oprint
        self.module_controller = module_controller
        self.cover_size = module_controller.orpheus_options.default_cover_options.resolution
        
        # Initialize API with debug setting from module settings
        self.session = BeatportApi()
        self.session.debug_enabled = module_controller.module_settings.get('debug', False)

        # Quality tier mapping
        self.quality_parse = {
            QualityEnum.MINIMUM: "low",      # 128k AAC
            QualityEnum.LOW: "low",          # 128k AAC
            QualityEnum.MEDIUM: "medium",    # 128k AAC
            QualityEnum.HIGH: "high",        # 256k AAC
            QualityEnum.LOSSLESS: "flac",    # FLAC
            QualityEnum.HIFI: "flac"         # FLAC
        }

        # Login using credentials from settings
        if not self.disable_subscription_check:
            self.login(
                module_controller.module_settings['username'],
                module_controller.module_settings['password']
            )

    def login(self, email: str, password: str):
        """Login and validate account"""
        login_data = self.session.auth(email, password)
        if login_data.get('error_description'):
            raise self.exception(login_data.get('error_description'))

        # Validate subscription using introspect endpoint
        subscription = self.session.get_subscription()
        
        # Check scopes in the introspection response
        scopes = subscription.get('scope', '').split()
        if 'user:dj' not in scopes:
            raise self.exception('Account does not have DJ/streaming permissions')
            
        # Check subscription type
        sub_type = subscription.get('subscription')
        if not sub_type:
            raise self.exception('No active subscription found')
            
        # Verify it's a LINK or LINK PRO subscription
        if sub_type not in ['bp_link', 'bp_link_pro']:
            raise self.exception('Account does not have a LINK or LINK PRO subscription')
            
        # Check features
        features = subscription.get('feature', [])
        required_features = [
            'feature:fulltrackplayback',
            'feature:cdnfulfillment',
            'feature:cdnfulfillment-link'
        ]
        
        missing_features = [f for f in required_features if f not in features]
        if missing_features:
            raise self.exception(f'Account missing required features: {", ".join(missing_features)}')

    @staticmethod
    def custom_url_parse(link: str):
        # First check if it's a library playlist URL
        library_match = re.search(r"https?://(www.)?beatport.com/library/playlists/(\d+)", link)
        if library_match:
            playlist_id = library_match.group(2)
            return MediaIdentification(
                media_type=DownloadTypeEnum.playlist,
                media_id=playlist_id,
                extra_kwargs={'is_library': True}  # Flag to use my/playlists endpoint
            )

        # Check if it's a genre chart URL
        genre_chart_match = re.search(r"/genre/[^/]+/(\d+)/hype-(\d+)", link)
        if genre_chart_match:
            genre_id = genre_chart_match.group(1)
            chart_type = genre_chart_match.group(2)
            chart_id = f"genre-{genre_id}-hype-{chart_type}"
            return MediaIdentification(
                media_type=DownloadTypeEnum.playlist,
                media_id=chart_id,
                extra_kwargs={'is_chart': True}
            )

        # Handle regular URLs
        match = re.search(r"https?://(www.)?beatport.com/(?:[a-z]{2}/)?"
                          r"(?P<type>track|release|artist|playlists|chart)/.+?/(?P<id>\d+)", link)

        if not match:
            raise ValueError("Invalid URL format")

        media_types = {
            'track': DownloadTypeEnum.track,
            'release': DownloadTypeEnum.album,
            'artist': DownloadTypeEnum.artist,
            'playlists': DownloadTypeEnum.playlist,
            'chart': DownloadTypeEnum.playlist
        }

        return MediaIdentification(
            media_type=media_types[match.group('type')],
            media_id=match.group('id'),
            extra_kwargs={'is_chart': match.group('type') == 'chart'}
        )

    def get_playlist_info(self, playlist_id: str, is_chart: bool = False, is_library: bool = False) -> PlaylistInfo:
        # get the DJ chart, user playlist, or library playlist
        if is_chart:
            playlist_data = self.session.get_chart(playlist_id)
            playlist_tracks_data = self.session.get_chart_tracks(playlist_id)
        elif is_library:
            playlist_data = self.session.get_library_playlist(playlist_id)
            playlist_tracks_data = self.session.get_library_playlist_tracks(playlist_id)
        else:
            playlist_data = self.session.get_playlist(playlist_id)
            playlist_tracks_data = self.session.get_playlist_tracks(playlist_id)

        cache = {'data': {}}

        # now fetch all the found total_items
        if is_chart:
            playlist_tracks = playlist_tracks_data.get('results')
        else:
            playlist_tracks = [t.get('track') for t in playlist_tracks_data.get('results')]

        total_tracks = playlist_tracks_data.get('count')
        for page in range(2, (total_tracks - 1) // 100 + 2):
            print(f'Fetching {len(playlist_tracks)}/{total_tracks}', end='\r')
            # get the DJ chart or user playlist
            if is_chart:
                playlist_tracks += self.session.get_chart_tracks(playlist_id, page=page).get('results')
            else:
                # unfold the track element
                playlist_tracks += [t.get('track')
                                    for t in self.session.get_playlist_tracks(playlist_id, page=page).get('results')]

        for i, track in enumerate(playlist_tracks):
            # add the track numbers
            track['track_number'] = i + 1
            track['total_tracks'] = total_tracks
            # add the modified track to the track_extra_kwargs
            cache['data'][track.get('id')] = track

        creator = 'User'
        if is_chart:
            creator = playlist_data.get('person').get('owner_name') if playlist_data.get('person') else 'Beatport'
            release_year = playlist_data.get('change_date')[:4] if playlist_data.get('change_date') else None
            cover_url = playlist_data.get('image').get('dynamic_uri')
        else:
            release_year = playlist_data.get('updated_date')[:4] if playlist_data.get('updated_date') else None
            # always get the first image of the four total images, why is there no dynamic_uri available? Annoying
            cover_url = playlist_data.get('release_images')[0]

        return PlaylistInfo(
            name=playlist_data.get('name'),
            creator=creator,
            release_year=release_year,
            duration=sum([t.get('length_ms', 0) // 1000 for t in playlist_tracks]),
            tracks=[t.get('id') for t in playlist_tracks],
            cover_url=self._generate_artwork_url(cover_url, self.cover_size),
            track_extra_kwargs=cache
        )

    @staticmethod
    def _generate_artwork_url(cover_url: str, size: int, max_size: int = 1400):
        # if more than max_size are requested, cap the size at max_size
        if size > max_size:
            size = max_size

        # check if it's a dynamic_uri, if not make it one
        res_pattern = re.compile(r'\d{3,4}x\d{3,4}')
        match = re.search(res_pattern, cover_url)
        if match:
            # replace the hardcoded resolution with dynamic one
            cover_url = re.sub(res_pattern, '{w}x{h}', cover_url)

        # replace the dynamic_uri h and w parameter with the wanted size
        return cover_url.format(w=size, h=size)

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, slug: str = None,
                       data=None, is_chart: bool = False) -> TrackInfo:
        if data is None:
            data = {}

        track_data = data[track_id] if track_id in data else self.session.get_track(track_id)

        album_id = track_data.get('release').get('id')
        album_data = {}
        error = None

        try:
            album_data = data[album_id] if album_id in data else self.session.get_release(album_id)
        except ConnectionError as e:
            # check if the album is region locked
            if 'Territory Restricted.' in str(e):
                error = f"Album {album_id} is region locked"

        track_name = track_data.get('name')
        track_name += f' ({track_data.get("mix_name")})' if track_data.get("mix_name") else ''

        release_year = track_data.get('publish_date')[:4] if track_data.get('publish_date') else None
        genres = [track_data.get('genre').get('name')]
        # check if a second genre exists
        genres += [track_data.get('sub_genre').get('name')] if track_data.get('sub_genre') else []

        extra_tags = {}
        if track_data.get('bpm'):
            extra_tags['BPM'] = track_data.get('bpm')
        if track_data.get('key'):
            extra_tags['Key'] = track_data.get('key').get('name')

        tags = Tags(
            album_artist=album_data.get('artists', [{}])[0].get('name'),
            track_number=track_data.get('number'),
            total_tracks=album_data.get('track_count'),
            upc=album_data.get('upc'),
            isrc=track_data.get('isrc'),
            genres=genres,
            release_date=track_data.get('publish_date'),
            copyright=f'© {release_year} {track_data.get("release").get("label").get("name")}',
            label=track_data.get('release').get('label').get('name'),
            extra_tags=extra_tags
        )

        if not track_data['is_available_for_streaming']:
            error = f'Track "{track_data.get("name")}" is not streamable!'
        elif track_data.get('preorder'):
            error = f'Track "{track_data.get("name")}" is not yet released!'

        quality = self.quality_parse[quality_tier]
        # Update bitrate mapping to match our quality levels
        bitrate = {
            "low": 128,      # 128k AAC
            "medium": 128,   # 128k AAC
            "high": 256,     # 256k AAC
            "flac": 1411     # FLAC
        }
        length_ms = track_data.get('length_ms')

        track_info = TrackInfo(
            name=track_name,
            album=album_data.get('name'),
            album_id=album_data.get('id'),
            artists=[a.get('name') for a in track_data.get('artists')],
            artist_id=track_data.get('artists')[0].get('id'),
            release_year=release_year,
            duration=length_ms // 1000 if length_ms else None,
            bitrate=bitrate[quality],
            bit_depth=16 if quality == "flac" else None,
            sample_rate=44.1,
            cover_url=self._generate_artwork_url(
                track_data.get('release').get('image').get('dynamic_uri'), self.cover_size),
            tags=tags,
            codec=CodecEnum.AAC if quality_tier not in {QualityEnum.HIFI, QualityEnum.LOSSLESS} else CodecEnum.FLAC,
            download_extra_kwargs={'track_id': track_id, 'quality_tier': quality_tier},
            error=error
        )

        return track_info

    def get_track_cover(self, track_id: str, cover_options: CoverOptions, data=None) -> CoverInfo:
        if data is None:
            data = {}

        track_data = data[track_id] if track_id in data else self.session.get_track(track_id)
        cover_url = track_data.get('release').get('image').get('dynamic_uri')

        return CoverInfo(
            url=self._generate_artwork_url(cover_url, cover_options.resolution),
            file_type=ImageFileTypeEnum.jpg)

    def get_track_download(self, track_id: str, quality_tier: QualityEnum) -> TrackDownloadInfo:
        """Get track download info"""
        try:
            # Map quality tier and get download URL
            quality = self.quality_parse[quality_tier]
            download_data = self.session.get_track_download(track_id, quality=quality)
            
            if not download_data or not download_data.get('download_url'):
                raise self.exception('Could not get download URL')

            # Return just the URL and let Orpheus handle the download
            return TrackDownloadInfo(
                download_type=DownloadEnum.URL,
                file_url=download_data['download_url'],
                different_codec=CodecEnum.AAC if quality != 'flac' else CodecEnum.FLAC
            )

        except Exception as e:
            if isinstance(e, self.exception):
                raise e
            raise self.exception(f'Download failed: {str(e)}')

    def get_album_info(self, album_id: str, data=None, is_chart: bool = False) -> AlbumInfo:
        """Get album info and its tracks"""
        # check if album is already in album cache
        if data is None:
            data = {}

        # Get album data
        album_data = data.get(album_id) if album_id in data else self.session.get_release(album_id)
        
        # Get track IDs first
        tracks_data = self.session.get_release_tracks(album_id)
        tracks = tracks_data.get('results', [])
        
        # Get total tracks count
        total_tracks = tracks_data.get('count', 0)
        
        # Fetch remaining tracks if any
        for page in range(2, (total_tracks - 1) // 100 + 2):
            print(f'Fetching {len(tracks)}/{total_tracks}', end='\r')
            more_tracks = self.session.get_release_tracks(album_id, page=page)
            tracks.extend(more_tracks.get('results', []))

        # Create cache for track data
        cache = {'data': {album_id: album_data}}
        for i, track in enumerate(tracks):
            # add the track numbers
            track['number'] = i + 1
            # add the modified track to the track_extra_kwargs
            cache['data'][track.get('id')] = track

        return AlbumInfo(
            name=album_data.get('name'),
            release_year=album_data.get('publish_date')[:4] if album_data.get('publish_date') else None,
            duration=sum([t.get('length_ms', 0) // 1000 for t in tracks]),
            upc=album_data.get('upc'),
            cover_url=self._generate_artwork_url(album_data.get('image').get('dynamic_uri'), self.cover_size),
            artist=album_data.get('artists')[0].get('name'),
            artist_id=album_data.get('artists')[0].get('id'),
            tracks=[t.get('id') for t in tracks],
            track_extra_kwargs=cache
        )