"""Parser for FM22XX."""

from .fm_plus import PlusParser


class FM22Parser(PlusParser):
    """FM22 Parser with alarm targets enabled."""

    MODEL = "FM22"

    def __init__(self) -> None:
        super().__init__()
        self.state.alarm_temperatures = [None, None]
