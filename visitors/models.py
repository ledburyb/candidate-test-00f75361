from __future__ import annotations

import datetime
import uuid
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from django.db import models
from django.db.models.deletion import CASCADE
from django.http.request import HttpRequest
from django.utils.timezone import now as tz_now
from django.utils.translation import gettext_lazy as _lazy

from .settings import VISITOR_QUERYSTRING_KEY, VISITOR_TOKEN_EXPIRY


class InvalidVisitorPass(Exception):
    pass


class VisitorManager(models.Manager):
    def get_and_update(self, uuid: int) -> Visitor:
        """Get a temporary visitor by uuid and decrement uses_remaining."""
        visitor = self.select_for_update().get(uuid=uuid)
        visitor.validate()
        if visitor.max_uses is not None:
            visitor.uses_remaining -= 1
            visitor.save(update_fields=["uses_remaining"])
        return visitor


class Visitor(models.Model):
    """A temporary visitor (betwixt anonymous and authenticated)."""

    DEFAULT_TOKEN_EXPIRY = datetime.timedelta(seconds=VISITOR_TOKEN_EXPIRY)

    objects = VisitorManager()

    uuid = models.UUIDField(default=uuid.uuid4)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(db_index=True)
    scope = models.CharField(
        max_length=100, help_text=_lazy("Used to map request to view function")
    )
    created_at = models.DateTimeField(default=tz_now)
    context = models.JSONField(
        null=True,
        blank=True,
        help_text=_lazy("Used to store arbitrary contextual data."),
    )
    last_updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text=_lazy(
            "After this time the link can no longer be used - "
            "defaults to VISITOR_TOKEN_EXPIRY."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_lazy(
            "Set to False to disable the visitor link and prevent further access."
        ),
    )
    max_uses = models.PositiveIntegerField(null=True)
    uses_remaining = models.PositiveIntegerField(null=True)

    class Meta:
        verbose_name = "Visitor pass"
        verbose_name_plural = "Visitor passes"

    def __str__(self) -> str:
        return f"Visitor pass for {self.email} ({self.scope})"

    def __repr__(self) -> str:
        return (
            f"<Visitor id={self.id} uuid='{self.uuid}' "
            f"email='{self.email}' scope='{self.scope}' "
            f"uses_remaining={self.uses_remaining}>"
        )

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        if not self.expires_at:
            self.expires_at = self.created_at + self.DEFAULT_TOKEN_EXPIRY
        if self.max_uses is not None and self.uses_remaining is None:
            # Default uses_remaining to the maximum
            self.uses_remaining = self.max_uses

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def session_data(self) -> str:
        return str(self.uuid)

    @property
    def has_expired(self) -> bool:
        """Return True if the expires_at timestamp has been passed (or not yet set)."""
        if not self.expires_at:
            return False
        return self.expires_at < tz_now()

    @property
    def out_of_uses(self) -> bool:
        """If using max_uses ensure uses remaining is greater than zero."""
        if self.max_uses is None:
            return False
        return self.uses_remaining == 0

    @property
    def is_valid(self) -> bool:
        """Return True if the token is active and not yet expired."""
        return self.is_active and not self.has_expired and not self.out_of_uses

    def validate(self) -> None:
        """Raise InvalidVisitorPass if inactive or expired."""
        if not self.is_active:
            raise InvalidVisitorPass("Visitor pass is inactive")
        if self.has_expired:
            raise InvalidVisitorPass("Visitor pass has expired")
        if self.out_of_uses:
            raise InvalidVisitorPass(
                "Visitor pass has already been used the maximum number of times"
            )

    def serialize(self) -> dict:
        """
        Return JSON-serializable representation.

        Useful for template context and session data.

        """
        return {
            "uuid": str(self.uuid),
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "email": self.email,
            "scope": self.scope,
            "context": self.context,
        }

    def tokenise(self, url: str) -> str:
        """Combine url with querystring token."""
        # from https://stackoverflow.com/a/2506477/45698
        parts = list(urlparse(url))
        query = parse_qs(parts[4])
        query.update({VISITOR_QUERYSTRING_KEY: self.uuid})
        parts[4] = urlencode(query)
        return urlunparse(parts)

    def deactivate(self) -> None:
        """Deactivate the token so it can no longer be used."""
        self.is_active = False
        self.save()

    def reactivate(self) -> None:
        """Reactivate the token so it can be reused."""
        self.is_active = True
        self.expires_at = tz_now() + self.DEFAULT_TOKEN_EXPIRY
        self.uses_remaining = self.max_uses
        self.save()


class VisitorLogManager(models.Manager):
    def create_log(self, request: HttpRequest, status_code: int) -> VisitorLog:
        """Extract values from HttpRequest and store locally."""
        return self.create(
            visitor=request.visitor,
            session_key=request.session.session_key or "",
            http_method=request.method,
            request_uri=request.path,
            query_string=request.META.get("QUERY_STRING", ""),
            http_user_agent=request.META.get("HTTP_USER_AGENT", ""),
            # we care about the domain more than the URL itself, so truncating
            # doesn't lose much useful information
            http_referer=request.META.get("HTTP_REFERER", ""),
            # X-Forwarded-For is used by convention when passing through
            # load balancers etc., as the REMOTE_ADDR is rewritten in transit
            remote_addr=(
                request.META.get("HTTP_X_FORWARDED_FOR")
                if "HTTP_X_FORWARDED_FOR" in request.META
                else request.META.get("REMOTE_ADDR")
            ),
            status_code=status_code,
        )


class VisitorLog(models.Model):
    """Log visitors."""

    visitor = models.ForeignKey(Visitor, related_name="visits", on_delete=CASCADE)
    session_key = models.CharField(blank=True, max_length=40)
    http_method = models.CharField(max_length=10)
    request_uri = models.URLField()
    remote_addr = models.CharField(max_length=100)
    query_string = models.TextField(blank=True)
    http_user_agent = models.TextField()
    http_referer = models.TextField()
    status_code = models.PositiveIntegerField("HTTP Response", default=0)
    timestamp = models.DateTimeField(default=tz_now)

    objects = VisitorLogManager()
