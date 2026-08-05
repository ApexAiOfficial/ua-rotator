"""Example Scrapy settings fragment."""

DOWNLOADER_MIDDLEWARES = {
    "apex_ua_rotator.scrapy.ScrapyUserAgentMiddleware": 90,
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
}

# The bundled global profile is used automatically.
UA_ROTATOR_SCOPE = "domain"
UA_ROTATOR_REFRESH_ON = []
UA_ROTATOR_OVERWRITE_EXISTING = False
UA_ROTATOR_MAX_KEY_CACHE = 4096

ROBOTSTXT_USER_AGENT = "ExampleCrawler/1.0"
