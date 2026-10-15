from albra_seed import Runtime, SeedConfig, create_app

runtime = Runtime(config=SeedConfig())
app = create_app(runtime)
