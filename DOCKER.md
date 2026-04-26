# Docker Run Guide

## Prerequisites

- Install Docker Desktop
- Make sure Docker Desktop is running

## Start the app

From the project root:

```powershell
docker compose up --build
```

The app will be available at:

```text
http://localhost:8000
```

## Notes

- The container runs `python manage.py migrate` automatically before starting the server.
- The Django project folder is mounted into the container, so local code changes are reflected immediately.
- The SQLite database stays in `vmanager/db.sqlite3`, so data persists between container restarts.
- The project still reads `vmanager/.env`, so keep `OPENROUTER_API_KEY` and any other secrets there.

## Stop the app

Press `Ctrl + C` in the terminal where Docker is running.

To stop and remove the container later:

```powershell
docker compose down
```
