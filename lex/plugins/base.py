"""Base plugin: reaches the network only through the shared session."""

from abc import ABC


class Plugin(ABC):
    kernel = None

    @property
    def session(self):
        """The one shared LexisSession owned by the kernel."""
        return self.kernel.session
