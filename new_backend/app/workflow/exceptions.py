class WorkflowError(Exception):
    """ワークフロー操作エラーの基底クラス"""


class InvalidTransition(WorkflowError):
    """許可されていない遷移"""


class PermissionDenied(WorkflowError):
    """ロール不一致"""


class CommentRequired(WorkflowError):
    """差戻しコメント未入力"""
