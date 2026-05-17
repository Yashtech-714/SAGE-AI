"""
app/models/olist.py
====================
SQLAlchemy ORM models for every Olist table.

Architecture decisions:
  1. DeclarativeBase (SQLAlchemy 2.x style) — explicit, type-annotated columns
  2. Every table has explicit primary keys and indexed foreign keys
  3. Relationships are bidirectional so the ORM can navigate joins
  4. Indexes are created on every FK column and commonly queried columns
     (e.g. order_status, customer_state) — critical for analytics performance
  5. All timestamp columns stored as TEXT in SQLite (ISO-8601 strings) because
     SQLite has no native DATETIME type; SQLAlchemy maps them transparently

Entity Relationship Summary
──────────────────────────────────────────────────────────────────────
customers  ──(1:N)──  orders  ──(1:N)──  order_items  ──(N:1)──  products
                          │                   │
                          │                   └──(N:1)──  sellers
                          ├──(1:N)──  order_payments
                          └──(1:N)──  order_reviews

products  ──(N:1)──  product_category_name_translation
sellers   ──(N:1)──  geolocation  (via zip_code_prefix)
customers ──(N:1)──  geolocation  (via zip_code_prefix)
──────────────────────────────────────────────────────────────────────
"""

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


# ── Base ─────────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Shared declarative base — all models inherit from this."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. GEOLOCATION
#    Root reference table — zip codes map to city / state / lat / lng.
#    NOTE: Multiple rows can share the same zip prefix (one-to-many raw).
#    We use a single-row-per-zip aggregated view at ingest time.
# ─────────────────────────────────────────────────────────────────────────────
class Geolocation(Base):
    __tablename__ = "geolocation"

    geolocation_zip_code_prefix: str = Column(String(10), primary_key=True)
    geolocation_lat: float = Column(Float)
    geolocation_lng: float = Column(Float)
    geolocation_city: str = Column(String(100))
    geolocation_state: str = Column(String(2), index=True)

    # Relationships (back-populated from Customers and Sellers)
    customers = relationship("Customer", back_populates="geolocation")
    sellers = relationship("Seller", back_populates="geolocation")


# ─────────────────────────────────────────────────────────────────────────────
# 2. CUSTOMERS
# ─────────────────────────────────────────────────────────────────────────────
class Customer(Base):
    __tablename__ = "customers"

    customer_id: str = Column(String(50), primary_key=True)
    customer_unique_id: str = Column(String(50), nullable=False, index=True)
    customer_zip_code_prefix: str = Column(
        String(10),
        ForeignKey("geolocation.geolocation_zip_code_prefix", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    customer_city: str = Column(String(100))
    customer_state: str = Column(String(2), index=True)

    # Relationships
    geolocation = relationship("Geolocation", back_populates="customers")
    orders = relationship("Order", back_populates="customer")

    __table_args__ = (
        Index("ix_customers_state_city", "customer_state", "customer_city"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. SELLERS
# ─────────────────────────────────────────────────────────────────────────────
class Seller(Base):
    __tablename__ = "sellers"

    seller_id: str = Column(String(50), primary_key=True)
    seller_zip_code_prefix: str = Column(
        String(10),
        ForeignKey("geolocation.geolocation_zip_code_prefix", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    seller_city: str = Column(String(100))
    seller_state: str = Column(String(2), index=True)

    # Relationships
    geolocation = relationship("Geolocation", back_populates="sellers")
    order_items = relationship("OrderItem", back_populates="seller")


# ─────────────────────────────────────────────────────────────────────────────
# 4. PRODUCT CATEGORY TRANSLATION
# ─────────────────────────────────────────────────────────────────────────────
class ProductCategoryTranslation(Base):
    __tablename__ = "product_category_name_translation"

    product_category_name: str = Column(String(100), primary_key=True)
    product_category_name_english: str = Column(String(100), nullable=False)

    # Relationships
    products = relationship("Product", back_populates="category_translation")


# ─────────────────────────────────────────────────────────────────────────────
# 5. PRODUCTS
# ─────────────────────────────────────────────────────────────────────────────
class Product(Base):
    __tablename__ = "products"

    product_id: str = Column(String(50), primary_key=True)
    product_category_name: str = Column(
        String(100),
        ForeignKey(
            "product_category_name_translation.product_category_name",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    product_name_lenght: int = Column(Integer)          # kept as-is from dataset
    product_description_lenght: int = Column(Integer)   # kept as-is from dataset
    product_photos_qty: int = Column(Integer)
    product_weight_g: float = Column(Float)
    product_length_cm: float = Column(Float)
    product_height_cm: float = Column(Float)
    product_width_cm: float = Column(Float)

    # Relationships
    category_translation = relationship(
        "ProductCategoryTranslation", back_populates="products"
    )
    order_items = relationship("OrderItem", back_populates="product")


# ─────────────────────────────────────────────────────────────────────────────
# 6. ORDERS  (central hub table — most joins originate here)
# ─────────────────────────────────────────────────────────────────────────────
class Order(Base):
    __tablename__ = "orders"

    order_id: str = Column(String(50), primary_key=True)
    customer_id: str = Column(
        String(50),
        ForeignKey("customers.customer_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_status: str = Column(String(30), nullable=False, index=True)
    order_purchase_timestamp: str = Column(String(30))          # TEXT ISO-8601
    order_approved_at: str = Column(String(30))
    order_delivered_carrier_date: str = Column(String(30))
    order_delivered_customer_date: str = Column(String(30))
    order_estimated_delivery_date: str = Column(String(30))

    # Relationships
    customer = relationship("Customer", back_populates="orders")
    order_items = relationship("OrderItem", back_populates="order")
    payments = relationship("OrderPayment", back_populates="order")
    reviews = relationship("OrderReview", back_populates="order")

    __table_args__ = (
        Index("ix_orders_status_purchase", "order_status", "order_purchase_timestamp"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. ORDER ITEMS  (bridge between orders ↔ products ↔ sellers)
# ─────────────────────────────────────────────────────────────────────────────
class OrderItem(Base):
    __tablename__ = "order_items"

    # Composite PK: one order can contain multiple items
    order_id: str = Column(
        String(50),
        ForeignKey("orders.order_id", ondelete="CASCADE"),
        primary_key=True,
    )
    order_item_id: int = Column(Integer, primary_key=True)   # 1, 2, 3 … per order
    product_id: str = Column(
        String(50),
        ForeignKey("products.product_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    seller_id: str = Column(
        String(50),
        ForeignKey("sellers.seller_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    shipping_limit_date: str = Column(String(30))
    price: float = Column(Float, nullable=False)
    freight_value: float = Column(Float, nullable=False)

    # Relationships
    order = relationship("Order", back_populates="order_items")
    product = relationship("Product", back_populates="order_items")
    seller = relationship("Seller", back_populates="order_items")


# ─────────────────────────────────────────────────────────────────────────────
# 8. ORDER PAYMENTS
# ─────────────────────────────────────────────────────────────────────────────
class OrderPayment(Base):
    __tablename__ = "order_payments"

    # Composite PK: an order can be paid in multiple sequential installments
    order_id: str = Column(
        String(50),
        ForeignKey("orders.order_id", ondelete="CASCADE"),
        primary_key=True,
    )
    payment_sequential: int = Column(Integer, primary_key=True)
    payment_type: str = Column(String(30), index=True)
    payment_installments: int = Column(Integer)
    payment_value: float = Column(Float, nullable=False)

    # Relationships
    order = relationship("Order", back_populates="payments")


# ─────────────────────────────────────────────────────────────────────────────
# 9. ORDER REVIEWS
# ─────────────────────────────────────────────────────────────────────────────
class OrderReview(Base):
    __tablename__ = "order_reviews"

    review_id: str = Column(String(50), primary_key=True)
    order_id: str = Column(
        String(50),
        ForeignKey("orders.order_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_score: int = Column(Integer, index=True)
    review_comment_title: str = Column(Text)
    review_comment_message: str = Column(Text)
    review_creation_date: str = Column(String(30))
    review_answer_timestamp: str = Column(String(30))

    # Relationships
    order = relationship("Order", back_populates="reviews")
