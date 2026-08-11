from pydantic import BaseModel


class PublicMessage(BaseModel):
    message: str


class WhoAmIResponse(BaseModel):
    name: str


class RoleAnimalResponse(BaseModel):
    role: str
    animal: str
