DO THIS TO RUN BACKEND

.\.venv\Scripts\Activate.ps1

First time only:

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m uvicorn app.main:app --reload --port 8001
