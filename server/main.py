from server.app import AppConfig, create_app


app = create_app(AppConfig.from_env())
