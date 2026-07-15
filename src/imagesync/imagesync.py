"""
This class represents an instance of an imagesync object.
It maintains the state of the sync job and an interface to the kubeapi instance of the object.
"""

from kubedantic.models.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta
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


class ImageSyncSpec(pydantic.BaseModel):
    """
    This class represents the specification of an ImageSync object.

    Attributes:
        source (RepoSpec): The source repository specification.
        destination (RepoSpec): The destination repository specification.
        frequencyMinutes (int): The frequency in minutes at which the sync job should run.
        allArchitectures (bool): Whether to sync all architectures. Defaults to False.
        preserveDigests (bool): Whether to preserve the digests of the images. Defaults to False.
        removeOnDelete (bool): Whether to remove the destination image on delete. Defaults to False
    """

    source: RepoSpec
    destination: RepoSpec
    frequencyMinutes: int
    allArchitectures: bool = False
    preserveDigests: bool = False
    removeOnDelete: bool = False


class ImageSyncCondition(pydantic.BaseModel):
    """
    This class represents a condition of an ImageSync object.

    Attributes:
        type (str): The type of the condition. Can be "Ready", "Syncing", or "Failed".
        status (str): The status of the condition. Can be "True", "False", or "Unknown".
        lastTransitionTime (str | None): The last time the condition transitioned from one status to another.
        reason (str | None): A brief reason for the condition's last transition.
        message (str | None): A human-readable message indicating details about the transition.
    """

    type: str
    status: str
    lastTransitionTime: str | None = None
    reason: str | None = None
    message: str | None = None


class ImageSyncStatus(pydantic.BaseModel):
    """
    This class represents the status of an ImageSync object.

    Attributes:
        ready (bool): Whether the ImageSync is ready. This implies a sync has completed and the destination image is available.
        lastSyncTime (str | None): The last time the sync job was run.
        lastSyncStatus (str | None): The status of the last sync job. Can be "Success", "Failed", or "Running".
        conditions (list[ImageSyncCondition]): A list of conditions representing the current state of the ImageSync.
    """

    ready: bool = False
    lastSyncTime: str | None = None
    lastSyncStatus: str | None = None
    conditions: list[ImageSyncCondition] = []


class ImageSync(pydantic.BaseModel):
    """
    This class represents an instance of an imagesync object.
    It maintains the state of the sync job and an interface to the kubeapi instance of the object.

    Attributes:
        metadata (ObjectMeta): The metadata of the ImageSync object, including name, namespace, labels, and annotations.
        spec (ImageSyncSpec): The specification of the ImageSync object, detailing source and destination repositories and sync parameters.
        status (ImageSyncStatus): The current status of the ImageSync object, including readiness, last sync time, and conditions.
    """

    metadata: ObjectMeta
    spec: ImageSyncSpec
    status: ImageSyncStatus

    def __init__(self, **data):
        """
        Initialize the ImageSync object with the provided data. Probably you meant to use model_validate instead of __init__ directly.

        Args:
            **data: Arbitrary keyword arguments representing the attributes of the ImageSync object.
        """
        super().__init__(**data)
