from app.main import app


def test_strategy_weights_route_registered():
    paths = {route.path for route in app.routes}
    assert "/api/strategy/weights" in paths
