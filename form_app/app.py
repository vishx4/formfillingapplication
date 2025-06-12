from flask import Flask
from form_app.routes import main_bp # Adjusted import
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

app.register_blueprint(main_bp)

if __name__ == '__main__':
    # To run this directly for development:
    # Ensure the parent directory of form_app is in PYTHONPATH
    # Or run as a module: python -m form_app.app
    # For simplicity in this context, we assume direct execution is desired
    # and the necessary Python path adjustments are handled by the execution environment.
    app.run(debug=True)
