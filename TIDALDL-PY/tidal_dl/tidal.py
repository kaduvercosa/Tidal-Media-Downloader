#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
tidal.py  —  reescrito para usar tidalapi como backend.

O clientId/clientSecret hardcoded do projeto original foi revogado
pelo Tidal em março/2026. Esta versão delega auth e stream ao tidalapi,
que mantém credenciais próprias e é atualizado regularmente.

Instalação:
    pip install tidalapi            (Python padrão)
    pip install tidalapi --break-system-packages   (Alpine / iSH)
"""

import concurrent.futures
import datetime
import re
import time
from typing import List, Optional

import requests

try:
    import tidalapi
except ImportError:
    raise ImportError(
        "\n[ERRO] tidalapi não encontrado.\n"
        "Execute:  pip install tidalapi\n"
        "         ou:  pip install tidalapi --break-system-packages  (Alpine/iSH)\n"
    )

from model import *
from settings import *
from enums import *


# ---------------------------------------------------------------------------
# Mapeamento de qualidade
# ---------------------------------------------------------------------------

_QUALITY_MAP = {
    AudioQuality.Normal: tidalapi.Quality.low_96k,
    AudioQuality.High:   tidalapi.Quality.low_320k,
    AudioQuality.HiFi:   tidalapi.Quality.high_lossless,
    AudioQuality.Master: tidalapi.Quality.hi_res,
    AudioQuality.Max:    tidalapi.Quality.hi_res_lossless,
}


# ---------------------------------------------------------------------------
# Helpers de conversão  (objetos tidalapi → modelos internos)
# ---------------------------------------------------------------------------

def _str_date(d) -> Optional[str]:
    if d is None:
        return None
    return str(d)  # datetime.date → "YYYY-MM-DD" automaticamente


def _to_artist(a) -> Artist:
    obj = Artist()
    obj.id      = getattr(a, 'id', None)
    obj.name    = getattr(a, 'name', '') or ''
    obj.picture = getattr(a, 'picture', None)
    obj.type    = getattr(a, 'type', None)
    return obj


def _to_album(a) -> Album:
    obj = Album()
    obj.id              = getattr(a, 'id', None)
    obj.title           = getattr(a, 'name', '') or getattr(a, 'title', '') or ''
    obj.duration        = getattr(a, 'duration', 0) or 0
    obj.numberOfTracks  = getattr(a, 'num_tracks', 0) or 0
    obj.numberOfVideos  = getattr(a, 'num_videos', 0) or 0
    obj.numberOfVolumes = getattr(a, 'num_volumes', 1) or 1
    obj.releaseDate     = _str_date(getattr(a, 'release_date', None))
    obj.type            = getattr(a, 'type', 'ALBUM') or 'ALBUM'
    obj.version         = getattr(a, 'version', None)
    obj.cover           = getattr(a, 'cover', None)
    obj.explicit        = getattr(a, 'explicit', False) or False
    obj.audioQuality    = getattr(a, 'audio_quality', 'LOSSLESS') or 'LOSSLESS'

    modes = getattr(a, 'audio_modes', None)
    if isinstance(modes, list):
        obj.audioModes = modes
    elif modes is not None:
        obj.audioModes = [str(modes)]
    else:
        obj.audioModes = []

    raw_artist = getattr(a, 'artist', None)
    if raw_artist:
        obj.artist = _to_artist(raw_artist)

    raw_artists = getattr(a, 'artists', None)
    if raw_artists:
        obj.artists = [_to_artist(x) for x in raw_artists]
    elif raw_artist:
        obj.artists = [obj.artist]
    else:
        obj.artists = []

    return obj


def _to_track(t) -> Track:
    obj = Track()
    obj.id                  = getattr(t, 'id', None)
    obj.title               = getattr(t, 'name', '') or getattr(t, 'title', '') or ''
    obj.duration            = getattr(t, 'duration', 0) or 0
    obj.trackNumber         = getattr(t, 'track_num', 0) or 0
    obj.volumeNumber        = getattr(t, 'volume_num', 1) or 1
    obj.trackNumberOnPlaylist = 0
    obj.version             = getattr(t, 'version', None)
    obj.isrc                = getattr(t, 'isrc', None)
    obj.explicit            = getattr(t, 'explicit', False) or False
    obj.audioQuality        = getattr(t, 'audio_quality', 'LOSSLESS') or 'LOSSLESS'
    obj.copyRight           = getattr(t, 'copyright', None)
    obj.allowStreaming      = True

    raw_artist = getattr(t, 'artist', None)
    if raw_artist:
        obj.artist = _to_artist(raw_artist)

    raw_artists = getattr(t, 'artists', None)
    if raw_artists:
        obj.artists = [_to_artist(a) for a in raw_artists]
    elif raw_artist:
        obj.artists = [obj.artist]
    else:
        obj.artists = []

    raw_album = getattr(t, 'album', None)
    if raw_album:
        obj.album = _to_album(raw_album)

    return obj


def _to_video(v) -> Video:
    obj = Video()
    obj.id          = getattr(v, 'id', None)
    obj.title       = getattr(v, 'name', '') or getattr(v, 'title', '') or ''
    obj.duration    = getattr(v, 'duration', 0) or 0
    obj.trackNumber = getattr(v, 'track_num', 0) or 0
    obj.releaseDate = _str_date(getattr(v, 'release_date', None))
    obj.explicit    = getattr(v, 'explicit', False) or False
    obj.quality     = getattr(v, 'quality', None)
    obj.allowStreaming = True

    raw_artist = getattr(v, 'artist', None)
    if raw_artist:
        obj.artist = _to_artist(raw_artist)

    raw_artists = getattr(v, 'artists', None)
    if raw_artists:
        obj.artists = [_to_artist(a) for a in raw_artists]
    elif raw_artist:
        obj.artists = [obj.artist]
    else:
        obj.artists = []

    raw_album = getattr(v, 'album', None)
    if raw_album:
        obj.album = _to_album(raw_album)

    return obj


def _to_playlist(p) -> Playlist:
    obj = Playlist()
    obj.uuid            = str(getattr(p, 'id', '') or '')
    obj.title           = getattr(p, 'name', '') or ''
    obj.numberOfTracks  = getattr(p, 'num_tracks', 0) or 0
    obj.numberOfVideos  = getattr(p, 'num_videos', 0) or 0
    obj.description     = getattr(p, 'description', '') or ''
    obj.duration        = getattr(p, 'duration', 0) or 0
    return obj


def _extract_stream_urls(manifest) -> List[str]:
    """Extrai lista de URLs de qualquer tipo de manifest do tidalapi."""
    # get_urls() — manifests DASH / multi-segmento
    if hasattr(manifest, 'get_urls') and callable(manifest.get_urls):
        try:
            urls = list(manifest.get_urls())
            if urls:
                return urls
        except Exception:
            pass

    # atributo .urls
    if hasattr(manifest, 'urls') and manifest.urls:
        return list(manifest.urls)

    # get_url() — URL única (BTS/CDN direto)
    if hasattr(manifest, 'get_url') and callable(manifest.get_url):
        try:
            url = manifest.get_url()
            if url:
                return [url]
        except Exception:
            pass

    # atributo .url
    if hasattr(manifest, 'url') and manifest.url:
        return [manifest.url]

    raise Exception("Não foi possível extrair URLs do manifest de stream")


# ---------------------------------------------------------------------------
# Classe principal TidalAPI
# ---------------------------------------------------------------------------

class TidalAPI(object):

    def __init__(self):
        self.key    = LoginKey()
        self.apiKey = {}   # mantido por compatibilidade de interface
        self._oauth_future: Optional[concurrent.futures.Future] = None

        # Inicializa sessão com a melhor qualidade disponível;
        # cada chamada de stream sobrescreve temporariamente.
        cfg = tidalapi.Config(quality=tidalapi.Quality.hi_res_lossless)
        self.session = tidalapi.Session(cfg)

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------

    def getDeviceCode(self) -> str:
        """
        Inicia o fluxo OAuth Device Code.
        Retorna a URL completa que o usuário deve visitar.
        """
        login, future = self.session.login_oauth()
        self.key.authCheckTimeout  = 300
        self.key.authCheckInterval = 3
        self._oauth_future = future
        return login.verification_uri_complete

    def checkAuthStatus(self) -> bool:
        """
        Verificação não-bloqueante: retorna True apenas quando o login
        foi concluído com sucesso no browser.
        """
        if self._oauth_future is None:
            return False
        if not self._oauth_future.done():
            return False
        try:
            self._oauth_future.result()   # relança exceção se houve erro
        except Exception:
            return False

        if not self.session.check_login():
            return False

        self._sync_key_from_session()
        return True

    def verifyAccessToken(self, accessToken: str) -> bool:
        """Tenta restaurar sessão a partir do token salvo."""
        if not accessToken:
            return False
        try:
            expiry = None
            if TOKEN.expiresAfter and float(TOKEN.expiresAfter) > 0:
                expiry = datetime.datetime.fromtimestamp(
                    float(TOKEN.expiresAfter), tz=datetime.timezone.utc
                )
            ok = self.session.load_oauth_session(
                "Bearer",
                accessToken,
                TOKEN.refreshToken or None,
                expiry,
            )
            if ok and self.session.check_login():
                self._sync_key_from_session()
                return True
            return False
        except Exception:
            return False

    def refreshAccessToken(self, refreshToken: str) -> bool:
        """
        Força refresh usando o refreshToken.
        Passa um expiry no passado para tidalapi fazer refresh proativamente.
        """
        if not refreshToken:
            return False
        try:
            past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
            ok = self.session.load_oauth_session(
                "Bearer",
                TOKEN.accessToken or "",
                refreshToken,
                past,
            )
            if ok and self.session.check_login():
                self._sync_key_from_session()
                return True
            return False
        except Exception:
            return False

    def loginByAccessToken(self, accessToken: str, userid=None):
        """Login manual via accessToken (opção 3 do menu)."""
        try:
            expiry = None
            if TOKEN.expiresAfter and float(TOKEN.expiresAfter) > 0:
                expiry = datetime.datetime.fromtimestamp(
                    float(TOKEN.expiresAfter), tz=datetime.timezone.utc
                )
            ok = self.session.load_oauth_session(
                "Bearer",
                accessToken,
                TOKEN.refreshToken or None,
                expiry,
            )
            if not ok or not self.session.check_login():
                raise Exception("Login falhou! Token inválido ou expirado.")
            if userid and str(self.session.user.id) != str(userid):
                raise Exception("Usuário diferente do esperado. Use seu próprio accessToken.")
            self._sync_key_from_session()
        except Exception as e:
            raise e

    def _sync_key_from_session(self):
        """Copia tokens da sessão tidalapi → self.key (interface original)."""
        self.key.userId       = getattr(self.session.user, 'id', None)
        self.key.countryCode  = getattr(self.session.user, 'country_code', 'US')
        self.key.accessToken  = self.session.access_token
        self.key.refreshToken = getattr(self.session, 'refresh_token', None)
        exp = getattr(self.session, 'expiry_time', None)
        if exp is not None:
            delta = exp - datetime.datetime.now(datetime.timezone.utc)
            self.key.expiresIn = max(0, int(delta.total_seconds()))
        else:
            self.key.expiresIn = 3600

    # ------------------------------------------------------------------
    # Metadados
    # ------------------------------------------------------------------

    def getAlbum(self, id) -> Album:
        return _to_album(self.session.album(int(id)))

    def getPlaylist(self, id) -> Playlist:
        return _to_playlist(self.session.playlist(str(id)))

    def getPlaylistSelf(self) -> List[Playlist]:
        return [_to_playlist(p) for p in self.session.user.playlists()]

    def getArtist(self, id) -> Artist:
        return _to_artist(self.session.artist(int(id)))

    def getTrack(self, id) -> Track:
        return _to_track(self.session.track(int(id)))

    def getVideo(self, id) -> Video:
        return _to_video(self.session.video(int(id)))

    def getMix(self, id):
        raw   = self.session.mix(str(id))
        items = raw.items()
        mix   = Mix()
        mix.id     = id
        mix.tracks = [_to_track(i) for i in items if isinstance(i, tidalapi.Track)]
        mix.videos = [_to_video(i) for i in items if isinstance(i, tidalapi.Video)]
        return None, mix

    def getItems(self, id, type: Type):
        if type == Type.Album:
            raw    = self.session.album(int(id)).tracks()
            tracks = [_to_track(t) for t in raw]
            videos = []
        elif type == Type.Playlist:
            raw    = self.session.playlist(str(id)).items()
            tracks = [_to_track(i) for i in raw if isinstance(i, tidalapi.Track)]
            videos = [_to_video(i) for i in raw if isinstance(i, tidalapi.Video)]
        elif type == Type.Mix:
            raw    = self.session.mix(str(id)).items()
            tracks = [_to_track(i) for i in raw if isinstance(i, tidalapi.Track)]
            videos = [_to_video(i) for i in raw if isinstance(i, tidalapi.Video)]
        else:
            raise Exception("Type inválido!")
        return tracks, videos

    def getArtistAlbums(self, id, includeEP=False) -> List[Album]:
        artist = self.session.artist(int(id))
        albums = [_to_album(a) for a in artist.get_albums()]
        if includeEP:
            albums += [_to_album(a) for a in artist.get_albums_ep_singles()]
        return albums

    def search(self, text: str, type: Type, offset: int = 0, limit: int = 10):
        models_map = {
            Type.Track:    [tidalapi.Track],
            Type.Album:    [tidalapi.Album],
            Type.Artist:   [tidalapi.Artist],
            Type.Playlist: [tidalapi.Playlist],
            Type.Video:    [tidalapi.Video],
            Type.Null:     [tidalapi.Track, tidalapi.Album,
                            tidalapi.Artist, tidalapi.Playlist, tidalapi.Video],
        }
        models = models_map.get(type, [tidalapi.Track])
        raw = self.session.search(text, models=models, limit=limit, offset=offset)

        result           = SearchResult()
        result.tracks    = SearchTracks()
        result.albums    = SearchAlbums()
        result.artists   = SearchArtists()
        result.playlists = SearchPlaylists()
        result.videos    = SearchVideos()

        result.tracks.items    = [_to_track(t)    for t in raw.get('tracks',    [])]
        result.albums.items    = [_to_album(a)    for a in raw.get('albums',    [])]
        result.artists.items   = [_to_artist(a)   for a in raw.get('artists',   [])]
        result.playlists.items = [_to_playlist(p) for p in raw.get('playlists', [])]
        result.videos.items    = [_to_video(v)    for v in raw.get('videos',    [])]
        return result

    def getSearchResultItems(self, result: SearchResult, type: Type):
        dispatch = {
            Type.Track:    result.tracks.items,
            Type.Video:    result.videos.items,
            Type.Album:    result.albums.items,
            Type.Artist:   result.artists.items,
            Type.Playlist: result.playlists.items,
        }
        return dispatch.get(type, [])

    def getLyrics(self, id) -> Lyrics:
        try:
            raw = self.session.track(int(id)).lyrics()
            obj = Lyrics()
            obj.trackId   = id
            obj.subtitles = getattr(raw, 'subtitles', '') or ''
            obj.lyrics    = getattr(raw, 'text', '') or ''
            return obj
        except Exception:
            return Lyrics()

    def getTrackContributors(self, id):
        try:
            raw   = self.session.track(int(id)).contributors()
            items = []
            for c in (raw or []):
                if isinstance(c, dict):
                    items.append(c)
                else:
                    items.append({
                        'name': getattr(c, 'name', ''),
                        'role': getattr(c, 'role', ''),
                    })
            return {'items': items}
        except Exception:
            return {'items': []}

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def getStreamUrl(self, id, quality: AudioQuality) -> StreamUrl:
        tidal_q = _QUALITY_MAP.get(quality, tidalapi.Quality.high_lossless)
        old_q   = self.session.audio_quality
        self.session.audio_quality = tidal_q
        try:
            manifest = self.session.track(int(id)).stream()

            ret = StreamUrl()
            ret.trackid = id

            # codec
            ret.codec = (
                getattr(manifest, 'codec',  None) or
                getattr(manifest, 'codecs', None) or ''
            )

            # chave de encriptação (MQA / algumas faixas DRM)
            ret.encryptionKey = (
                getattr(manifest, 'encryption_key', None) or
                getattr(manifest, 'key_id', None) or ''
            )

            # qualidade efectiva retornada
            q_val = tidal_q.value if hasattr(tidal_q, 'value') else str(tidal_q)
            ret.soundQuality = (
                getattr(manifest, 'audio_quality', None) or q_val
            )

            ret.urls = _extract_stream_urls(manifest)
            ret.url  = ret.urls[0] if ret.urls else ''
            return ret
        finally:
            self.session.audio_quality = old_q

    def getVideoStreamUrl(self, id, quality: VideoQuality) -> VideoStreamUrl:
        video = self.session.video(int(id))

        m3u8 = ''
        try:
            if hasattr(video, 'get_url'):
                m3u8 = video.get_url() or ''
            elif hasattr(video, 'stream'):
                urls = _extract_stream_urls(video.stream())
                m3u8 = urls[0] if urls else ''
        except Exception as e:
            raise Exception(f"Falha ao obter URL de vídeo: {e}")

        ret             = VideoStreamUrl()
        ret.m3u8Url     = m3u8
        ret.codec       = 'mp4'
        ret.resolution  = str(quality.value)
        ret.resolutions = [str(quality.value)]
        return ret

    # ------------------------------------------------------------------
    # Utilitários
    # ------------------------------------------------------------------

    def getCoverUrl(self, sid, width="320", height="320") -> str:
        if not sid:
            return ""
        # tidalapi guarda como UUID com traços; URL do CDN usa barras
        return f"https://resources.tidal.com/images/{sid.replace('-', '/')}/{width}x{height}.jpg"

    def getCoverData(self, sid, width="320", height="320") -> bytes:
        url = self.getCoverUrl(sid, width, height)
        try:
            return requests.get(url, timeout=10).content
        except Exception:
            return b''

    def getArtistsName(self, artists=[]) -> str:
        if not artists:
            return ''
        if isinstance(artists, list):
            return ", ".join(a.name for a in artists if hasattr(a, 'name'))
        return getattr(artists, 'name', '')

    def getFlag(self, data, type: Type, short=True, separator=" / ") -> str:
        master = atmos = explicit = False

        if type in (Type.Album, Type.Track):
            q = getattr(data, 'audioQuality', '') or ''
            if q in ("HI_RES", "HI_RES_LOSSLESS"):
                master = True
            modes = getattr(data, 'audioModes', []) or []
            if type == Type.Album and "DOLBY_ATMOS" in modes:
                atmos = True
            if getattr(data, 'explicit', False):
                explicit = True
        elif type == Type.Video:
            if getattr(data, 'explicit', False):
                explicit = True

        if not master and not atmos and not explicit:
            return ""
        parts = []
        if master:   parts.append("M" if short else "Master")
        if atmos:    parts.append("A" if short else "Dolby Atmos")
        if explicit: parts.append("E" if short else "Explicit")
        return separator.join(parts)

    def parseUrl(self, url: str):
        if "tidal.com" not in url:
            return Type.Null, url
        url_lower = url.lower()
        for item in Type:
            name = item.name.lower()
            if name + '/' in url_lower:
                idx  = url_lower.find(name + '/')
                rest = url_lower[idx + len(name) + 1:]
                sid  = re.split(r'[/?#]', rest)[0]
                return item, sid
        return Type.Null, url

    def getTypeData(self, id, type: Type):
        dispatch = {
            Type.Album:    self.getAlbum,
            Type.Artist:   self.getArtist,
            Type.Track:    self.getTrack,
            Type.Video:    self.getVideo,
            Type.Playlist: self.getPlaylist,
            Type.Mix:      self.getMix,
        }
        fn = dispatch.get(type)
        if fn:
            return fn(id)
        return None

    def getByString(self, string: str):
        if not string:
            raise Exception("Digite algo.")
        etype, sid = self.parseUrl(string)
        for item in Type:
            if etype != Type.Null and etype != item:
                continue
            if item == Type.Null:
                continue
            try:
                obj = self.getTypeData(sid, item)
                if obj is not None:
                    return item, obj
            except Exception:
                continue
        raise Exception("Nenhum resultado.")


# ---------------------------------------------------------------------------
# Singleton global (compatível com o restante do código)
# ---------------------------------------------------------------------------
TIDAL_API = TidalAPI()