import os

bind = f"0.0.0.0:{os.environ.get('PORT', '3522')}"
workers = 1
threads = 4
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
