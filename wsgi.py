import os

from app import app

if app.debug or app.config.get("DEBUG"):
	raise RuntimeError("Flask debug mode must be disabled for WSGI startup.")

if not app.config.get("SESSION_COOKIE_SECURE"):
	raise RuntimeError("Set FLASK_ENV=production before WSGI startup to require Secure session cookies.")

secret_key = app.config.get("SECRET_KEY")
if (
	not isinstance(secret_key, str)
	or len(secret_key.encode("utf-8")) < 32
	or secret_key == "mannat-moon-dev-secret-change-me"
):
	raise RuntimeError("Set SECRET_KEY to a unique random value of at least 32 bytes before WSGI startup.")


def serve():
	from waitress import serve as waitress_serve

	try:
		port = int(os.environ.get("PORT", "5020"))
	except ValueError as error:
		raise RuntimeError("PORT must be a valid TCP port number.") from error
	if not 1 <= port <= 65535:
		raise RuntimeError("PORT must be a valid TCP port number.")
	waitress_serve(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
	serve()
