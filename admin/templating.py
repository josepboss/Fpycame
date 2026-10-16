"""One template environment for all admin routes and shared filters."""
from datetime import datetime
from fastapi.templating import Jinja2Templates


def timestamp_to_str(value: int | float | str) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError, OverflowError, OSError):
        return 'N/A'


templates = Jinja2Templates(directory='admin/templates')
templates.env.filters['timestamp_to_str'] = timestamp_to_str
