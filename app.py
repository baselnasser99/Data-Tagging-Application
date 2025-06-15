from application import create_app

app = create_app()

# Run an application by Python
if __name__ == "__main__":
    app.run(debug=True)