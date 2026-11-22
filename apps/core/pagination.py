from rest_framework.pagination import CursorPagination


class PublicIdCursorPagination(CursorPagination):
    """Default list pagination (architecture §6): newest first, opaque cursor.

    Ordered by the UUIDv7 public ID, which is unique and time-ordered, so cursors are
    stable and never contain internal integer IDs.
    """

    ordering = "-public_id"
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
