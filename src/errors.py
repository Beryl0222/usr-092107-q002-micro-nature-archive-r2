"""档案服务的领域错误。"""


class ArchiveError(Exception):
    """所有档案规则违例的基类。"""


class ValidationError(ArchiveError):
    """事件未通过契约校验。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("；".join(errors))


class DuplicateEventError(ArchiveError):
    """同一 event_id 被再次提交。重复投稿不得形成第二次观察。"""


class VersionConflictError(ArchiveError):
    """聚合版本不是上一版本加一（事件身份约定）。"""


class DuplicateSubmissionError(ArchiveError):
    """同一投稿去重键已经登记为一次观察。"""

    def __init__(self, submission_id: str, observation_id_value: str):
        self.submission_id = submission_id
        self.observation_id = observation_id_value
        super().__init__(f"投稿 {submission_id} 已登记为观察 {observation_id_value}")


class UnknownAggregateError(ArchiveError):
    """引用了尚不存在的观察或资产。"""


class LicenseStateError(ArchiveError):
    """许可不存在或已撤回，不能再次撤回或授权。"""


class PublicationStateError(ArchiveError):
    """出版冻结相关规则违例。"""
