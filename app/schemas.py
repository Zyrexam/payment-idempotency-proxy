from pydantic import BaseModel, Field, field_validator
from typing import Optional


class PaymentRequest(BaseModel):    
    amount: float = Field(
        ...,
        gt=0,
        le=10000,
        description="Payment amount in dollars (max $10,000)"
    )
    
    currency: str = Field(
        "USD",
        pattern="^[A-Z]{3}$",
        description="ISO currency code (USD, EUR, GBP, etc)"
    )
    
    source: str = Field(
        ...,
        min_length=1,
        description="Payment source identifier (card_xxx, bank_xxx, crypto_xxx)"
    )
    
    description: Optional[str] = Field(
        None,
        max_length=255,
        description="Optional payment description"
    )
    
    metadata: Optional[dict] = Field(
        default_factory=dict,
        description="Additional metadata for the payment"
    )
    
    @field_validator('amount')
    @classmethod
    def validate_amount(cls, v: float) -> float:
        return round(v, 2)
    
    @field_validator('source')
    @classmethod
    def validate_source(cls, v: str) -> str:
        allowed_prefixes = ['card_', 'bank_', 'crypto_']
        
        if not any(v.startswith(prefix) for prefix in allowed_prefixes):
            raise ValueError('Source must start with card_, bank_, or crypto_')
        
        if len(v) <= len(v.split('_')[0]) + 1:
            raise ValueError('Source must have an identifier after the prefix')
        
        return v


class PaymentResponse(BaseModel):
    status: str
    transaction_id: str
    amount: float
    currency: str
    timestamp: str
    description: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    idempotency_key: Optional[str] = None