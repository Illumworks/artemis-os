from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EventDateTime(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    date_time: str | None = Field(None, alias="dateTime")
    date: str | None = None
    time_zone: str | None = Field(None, alias="timeZone")


class EventAttendee(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    email: str
    display_name: str | None = Field(None, alias="displayName")
    response_status: str | None = Field(None, alias="responseStatus")


class Calendar(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    summary: str
    description: str | None = None
    time_zone: str | None = Field(None, alias="timeZone")
    primary: bool = False


class Event(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    summary: str | None = None
    description: str | None = None
    start: EventDateTime
    end: EventDateTime
    attendees: list[EventAttendee] = []
    status: str | None = None
    html_link: str | None = Field(None, alias="htmlLink")
