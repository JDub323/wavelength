"""MySQL connection settings. Edit the defaults here, or set the environment variables."""
import os

DB = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "ikeating"),
    "password": os.environ.get("DB_PASSWORD", "goirish"),
    "database": os.environ.get("DB_NAME", "ikeating"),
}
