# run.py
# Run this file to start the local development server
# Right-click → Run 'run' in PyCharm
# Then open your browser at: http://localhost:8000

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )