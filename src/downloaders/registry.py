from .alice import AliceDownloader
from .belle2 import Belle2Downloader
from .cms import CmsDownloader
from .ligo import LigoDownloader

DOWNLOADERS = {
    "cms_csv": CmsDownloader,
    "alice_html": AliceDownloader,
    "belle2_html": Belle2Downloader,
    "ligo_html": LigoDownloader,
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
