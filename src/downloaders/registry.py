from .alice import AliceDownloader
from .belle2 import Belle2Downloader
from .cms import CmsDownloader
from .inspire_collab import InspireCollabDownloader
from .ligo import LigoDownloader
from .openalex_search import OpenAlexSearchDownloader

DOWNLOADERS = {
    "cms_csv": CmsDownloader,
    "alice_html": AliceDownloader,
    "belle2_html": Belle2Downloader,
    "ligo_html": LigoDownloader,
    "inspire_collab": InspireCollabDownloader,
    "openalex_search": OpenAlexSearchDownloader,
}


def get_downloader(community_config):
    key = community_config["downloader"]
    try:
        cls = DOWNLOADERS[key]
    except KeyError:
        raise ValueError(
            f"Unknown downloader '{key}' for community '{community_config['name']}'. "
            f"Registered: {list(DOWNLOADERS)}"
        )
    return cls(community_config)
