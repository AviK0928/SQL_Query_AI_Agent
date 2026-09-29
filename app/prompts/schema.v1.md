Table: customers
  id           INTEGER  primary key
  name         TEXT
  email        TEXT
  city         TEXT
  signup_date  TEXT     ISO date, 'YYYY-MM-DD'

Table: products
  id        INTEGER  primary key
  name      TEXT
  category  TEXT     one of: Electronics, Accessories, Furniture, Stationery
  price     REAL     current list price, in INR

Table: orders
  id           INTEGER  primary key
  customer_id  INTEGER  -> customers.id
  order_date   TEXT     ISO date, 'YYYY-MM-DD'
  status       TEXT     one of: delivered, shipped, pending, cancelled

Table: order_items
  id          INTEGER  primary key
  order_id    INTEGER  -> orders.id
  product_id  INTEGER  -> products.id
  quantity    INTEGER
  unit_price  REAL     price actually paid, may differ from products.price
