from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

class BenchmarkType(str, Enum):
    BUGFIX = 'bugfix'
    FEATURE = 'feature'
    REFACTORING = 'refactoring'
    TEST_GENERATION = 'test_generation'
    REGRESSION_RECOVERY = 'regression_recovery'


class Benchmark(BaseModel):
    pass