Your previous SQLite query failed. Return one corrected SELECT query and
nothing else -- no explanation, no markdown, no trailing semicolon.

DATABASE SCHEMA
$schema


Use only the columns listed above. If the question cannot be answered from
this schema, reply with exactly $out_of_scope_token.

RULES
1. Revenue, spend, turnover and order totals use order_items.unit_price *
   quantity, not products.price, because unit_price is what the customer
   actually paid.
2. Unless the user says otherwise, exclude orders with status 'cancelled' from
   revenue, spend, turnover and units sold. Cancelled orders were still placed,
   so include them when counting or listing orders.
