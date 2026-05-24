# Personal Scripts

A collection of automation scripts for my personal computers and home server.

Each script lives in its own folder and is self-contained — setup instructions are in the folder's own README or CLAUDE.md file.

## Scripts

| Folder | What it does |
|--------|-------------|
| `mysql-backup` | Weekly automated backup of a MySQL database running in Docker. Zips the backup and syncs it to iCloud. Sends a push notification on success or failure. |

## General setup

Each script has its own configuration file that is **not** stored in git (it contains passwords and private keys). A template is provided in each folder — copy it and fill in your values.
