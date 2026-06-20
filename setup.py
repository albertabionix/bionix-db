# This file was developed with the help of Google AI

from setuptools import setup, find_packages

setup(
    name='bionixdb', # Must be a unique name on PyPI
    version='0.1.0',
    description='Library used to access Alberta Bionix databases.',
    author='Lance Quinto',
    packages=find_packages(where='src'), # Specifies where to look for packages
    package_dir={'': 'src'}, # Tells setuptools that packages are under src
    package_data={'bionix_db': ['credentials.json']}, # Bundle the shared OAuth client secret
    include_package_data=True,
    install_requires=[
        "numpy",
        "pandas",
        "google-auth",
        "google-auth-oauthlib",
        "google-auth-httplib2",
        "google-api-python-client",
    ],
)
