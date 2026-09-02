from .base import PostResult, Publisher
from .instagram import InstagramPublisher
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher

PUBLISHERS = {
    "youtube": YouTubePublisher,
    "tiktok": TikTokPublisher,
    "instagram": InstagramPublisher,
}

__all__ = ["PUBLISHERS", "PostResult", "Publisher",
           "YouTubePublisher", "TikTokPublisher", "InstagramPublisher"]
