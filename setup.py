from setuptools import setup, find_packages
import pathlib

# SCM version (setuptools_scm; git tag v0.3.3)
from setuptools_scm import get_version

# README stub if miss (build safe; no FileNotFoundError)
readme_path = pathlib.Path('README.md')
if not readme_path.exists():
    readme_path.write_text('Crypto Futures Dashboard (Modified v0.3.3 Fork)\n\nReal-time monitoring for crypto futures OI, L/S ratios, and deltas.', encoding='utf-8')

setup(
    name='futuresboard',
    version=get_version(root='.', relative_to=__file__),
    description='Crypto Futures Dashboard (Modified v0.3.3 Fork)',
    long_description=readme_path.read_text(encoding='utf-8'),
    long_description_content_type='text/markdown',
    author='Your Name',
    author_email='your@email.com',
    packages=find_packages('src'),
    package_dir={'': 'src'},
    install_requires=[
        'flask',
        'flask-socketio',
        'flask-cors',
        'sqlalchemy',
        'ccxt',
        'python-dotenv',
        'requests',
        'aiohttp',
    ],
    extras_require={
        'dev': [
            'pytest',
            'alembic',
            'setuptools_scm',
        ],
    },
    entry_points={
        'console_scripts': [
            'futuresboard = futuresboard.app:main',
        ],
    },
    python_requires='>=3.12',
)