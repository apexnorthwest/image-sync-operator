"""
This class represents an instance of an imagesync object.
It maintains the state of the sync job and an interface to the kubeapi instance of the object.
"""

import pydantic


class RepoSpec(pydantic.BaseModel):
    """
    This class represents the specification of a repository to be synced.

    Attributes:
        image (str): The image to be synced.
        registryLoginSecret (str | None): The name of the Kubernetes secret containing the registry login creds.
    """

    image: str
    registryLoginSecret: str | None = None


class ImageSync(pydantic.BaseModel):
    """
    This class represents an instance of an imagesync object.
    It maintains the state of the sync job and an interface to the kubeapi instance of the object.

    Attributes:
        source (RepoSpec): The source repository specification.
        destination (RepoSpec): The destination repository specification.
        frequencyMinutes (int): The frequency in minutes at which the sync job should run.
        allArchitectures (bool): Whether to sync all architectures. Defaults to False.
        removeOnDelete (bool): Whether to remove the destination image on delete. Defaults to False
    """

    source: RepoSpec
    destination: RepoSpec
    frequencyMinutes: int
    allArchitectures: bool = False
    removeOnDelete: bool = False
