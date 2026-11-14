"""A complete spider using the middleware, runnable without a Scrapy project.

    python -m pip install "Scrapy>=2.11,<3"
    scrapy runspider examples/quotes_spider.py -O quotes.json

quotes.toscrape.com is a public sandbox maintained for scraping practice.
"""

from __future__ import annotations

import scrapy


class QuotesSpider(scrapy.Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com/"]
    custom_settings = {
        "DOWNLOADER_MIDDLEWARES": {
            "apex_ua_rotator.scrapy.ScrapyUserAgentMiddleware": 90,
            "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
        },
        # One coherent identity per site; a retried request gets a fresh one.
        "UA_ROTATOR_SCOPE": "domain",
        "UA_ROTATOR_REFRESH_ON": ["retry"],
        "ROBOTSTXT_OBEY": True,
        "ROBOTSTXT_USER_AGENT": "ApexUARotatorExample",
        "AUTOTHROTTLE_ENABLED": True,
    }

    def parse(self, response):
        for quote in response.css("div.quote"):
            yield {
                "text": quote.css("span.text::text").get(),
                "author": quote.css("small.author::text").get(),
                "user_agent": response.request.headers.get("User-Agent", b"").decode(),
            }
        yield from response.follow_all(css="li.next a", callback=self.parse)
