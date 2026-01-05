# awscore
Core utilities for AWS and data validation/processing.


## Developer workflow

### Create .env file once
echo "LOCAL_ENV=dev" > .env

### Login to AWS SSO
aws sso login --profile my-company-dev

### Develop your python code and import 

```python
import aws_core
# or
from aws_core.core.config import Config
```

### Run your code - automatically uses dev profile
python app.py

# Usage

```python

from aws_core.aws.secrets import SecretsManager

secrets = SecretsManager()

# Get postgres credentials from project config file (e.g., "pyproject.toml")
# and automatically selects the correct secret name for the environment
# which the code detects (or is explicitly told) to run.
db_creds = secrets.get_secret('postgres')
db_username = db_creds['username']
db_password = db_creds["password"]

```