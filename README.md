# WORK-IN-PROGRESS: ANTCollab_Websocket

This project can be used to connect to the ANTSCollab_GUI project, being run from the same network on a different computer via websockets. The script & actions received will be processed and stored, so that fax experiences can be updated accordingly.

## First Installation
1. Create a virtual environmnet with `pip -m venv .venv`
2. Activate virtual environment with `source .venv/bin/activate`
3. Install packages from `pyproject.toml` with `pip install -e`
4. Run `python serve.py` to start the websocket server

# Subsequent Runs
1. Activate virtual envrionment with `source.venv/bin/activate`
2. Run `python serve.py` to start the websocket server

