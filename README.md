# Event Dashboard

A modular, real-time breaking news dashboard powered by Bluesky feeds.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the server
python app.py

# 3. Open in browser
#    Splash page:  http://localhost:5000/
#    Dashboard:    http://localhost:5000/dashboard
```

## Architecture

```
event-dashboard/
├── app.py              # Flask server + API routes
├── bluesky_feed.py     # Bluesky AT Protocol feed manager
├── event_detector.py   # Pluggable event detection engine
├── index.html          # Splash / hub page
├── dashboard.html      # 3-panel live dashboard
└── requirements.txt
```

## Adding New Feeds (bluesky_feed.py)

Add entries to `FEED_CONFIG`:

```python
{
    'id':      'my_feed',
    'name':    'My Topic',
    'type':    'search',
    'query':   'search terms here',
    'limit':   20,
    'enabled': True,
}
```

## Adding New Detection Strategies (event_detector.py)

Subclass `DetectionStrategy` and register it:

```python
class MyStrategy(DetectionStrategy):
    name = "my_strategy"
    
    def analyze(self, posts, existing_events):
        # Return list of new event dicts
        return []

# In EventDetector.__init__:
self.strategies.append(MyStrategy())
```

## API Endpoints

| Endpoint              | Method | Description                     |
|-----------------------|--------|---------------------------------|
| `/api/posts`          | GET    | Cached Bluesky posts            |
| `/api/events`         | GET    | Detected breaking events        |
| `/api/status`         | GET    | System health + module status   |
| `/api/feeds`          | GET    | Active feed configuration       |
| `/api/feeds/refresh`  | POST   | Force immediate feed refresh    |

## Bluesky Rate Limits

- Public search API: ~3000 req/5 min
- Default poll: every 30s across 3 feeds = 6 req/30s = well within limits
- No authentication required for public feeds

## Roadmap

- [ ] Twitter/X filtered stream
- [ ] RSS aggregator (AP, Reuters, BBC)
- [ ] AI summarization via Claude API  
- [ ] Geo-tagging and map view
- [ ] Sentiment analysis per event
- [ ] Webhook alerts (Slack, Discord)
- [ ] Mastodon firehose
- [ ] Telegram channel monitoring
