"""
Pydantic Schemas - Define the shape of our request/response data

Pydantic validates data types automatically. For example, if someone sends
"amount": "not a number", Pydantic will reject it with a clear error.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional


class PaymentRequest(BaseModel):
    """
    What the client MUST send to create a payment.
    
    Example valid request:
    {
        "amount": 99.99,
        "currency": "USD",
        "source": "card_123456",
        "description": "Order #1234"
    }
    """
    
    amount: float = Field(
        ...,  # ... means required
        gt=0,  # greater than 0
        le=10000,  # less than or equal to 10000
        description="Payment amount in dollars (max $10,000)"
    )
    
    currency: str = Field(
        "USD",  # default value
        pattern="^[A-Z]{3}$",  # must be 3 uppercase letters
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
    
    # Custom validation - runs automatically
    @field_validator('amount')
    @classmethod
    def validate_amount(cls, v: float) -> float:
        """Ensure amount has exactly 2 decimal places"""
        return round(v, 2)
    
    @field_validator('source')
    @classmethod
    def validate_source(cls, v: str) -> str:
        """Ensure source has correct prefix"""
        allowed_prefixes = ['card_', 'bank_', 'crypto_']
        
        # Check if source starts with any allowed prefix
        if not any(v.startswith(prefix) for prefix in allowed_prefixes):
            raise ValueError('Source must start with card_, bank_, or crypto_')
        
        # Ensure source has content after prefix
        if len(v) <= len(v.split('_')[0]) + 1:
            raise ValueError('Source must have an identifier after the prefix')
        
        return v


class PaymentResponse(BaseModel):
    """
    What the client receives after a successful payment
    """
    status: str
    transaction_id: str
    amount: float
    currency: str
    timestamp: str
    description: Optional[str] = None


class ErrorResponse(BaseModel):
    """
    Standard error format our API returns
    """
    error: str
    detail: Optional[str] = None
    idempotency_key: Optional[str] = None