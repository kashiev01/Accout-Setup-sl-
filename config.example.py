# Template of config.py with no secrets (the Docker image uses this; values come
# from the environment, see .env.example). Every value can be overridden by an
# environment variable of the same name.
import os

ADMIN_URL = os.environ.get("ADMIN_URL", "")
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

BILLING_URL = os.environ.get("BILLING_URL", "")
BILLING_LOGIN = os.environ.get("BILLING_LOGIN", "")
BILLING_PASSWORD = os.environ.get("BILLING_PASSWORD", "")

SLAPI_URL = os.environ.get("SLAPI_URL", "")
SLAPI_LOGIN = os.environ.get("SLAPI_LOGIN", "")
SLAPI_PASSWORD = os.environ.get("SLAPI_PASSWORD", "")

# Pause between accounts (seconds)
DELAY_BETWEEN_ACCOUNTS = float(os.environ.get("DELAY_BETWEEN_ACCOUNTS", "1.5"))

# Run browser visibly (True) or headless (False)
HEADLESS = os.environ.get("HEADLESS", "True").lower() in ("1", "true", "yes")
