import os

from application import create_app

app = create_app()

# Run an application by Python. Debug mode only on request: its error page lets visitors run code.
if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
