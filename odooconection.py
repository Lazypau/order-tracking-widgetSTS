from flask import Flask, jsonify, request
import os
from flask_cors import CORS
import xmlrpc.client
from datetime import datetime, date
import re

# Initialize Flask app and enable CORS
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "https://lazypau.github.io"}})

# Fetch Odoo credentials from environment variables
url = os.environ.get('ODOO_URL')
db = os.environ.get('ODOO_DB')
username = os.environ.get('ODOO_USERNAME')
password = os.environ.get('ODOO_PASSWORD')

# Ensure environment variables are available
if not url or not db or not username or not password:
    raise ValueError("Missing Odoo environment variables. Please set ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD.")

# Connect to Odoo via XML-RPC
common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
common.version()
uid = common.authenticate(db, username, password, {})
models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

# Check read access on res.partner
models.execute_kw(db, uid, password, 'res.partner', 'check_access_rights', ['read'], {'raise_exception': False})

# Function to remove numbers inside square brackets from product name
def clean_product_name(product_name):
    # Remove anything inside square brackets including the brackets
    cleaned_name = re.sub(r'\[.*?\]', '', product_name).strip()
    return cleaned_name


@app.route('/')
def home():
    return "Hello, the server is live!"


@app.route('/order-status', methods=['GET'])
def get_order_status():
    # Get the order name from the request
    order_name = request.args.get('order_name')

    # Fetch the status of the order from Odoo
    RawStatus = models.execute_kw(db, uid, password, 'stock.picking', 'search_read',
                                  [[['origin', '=', order_name]]],
                                  {'fields': ['state']})

    # Fetch the products of the order from Odoo
    Product_list = models.execute_kw(db, uid, password, 'sale.order.line', 'search_read',
                                     [[['order_id', '=', order_name]]],
                                     {'fields': ['product_id', 'name', 'product_uom_qty']})

    # Create a list to store product information along with their edition date
    Product_with_date = []

    # Loop through each product in the order
    for product in Product_list:
        product_id = product['product_id'][0]  # Get the product's ID from 'product_id' (first item is the ID)
        
        # Step 1: Query the product.product model using the product_id
        product_data = models.execute_kw(db, uid, password, 'product.product', 'search_read',
                                        [[['id', '=', product_id]]],
                                        {'fields': ['product_tmpl_id']})
        
        if product_data and product_data[0].get('product_tmpl_id'):
            product_template_id = product_data[0]['product_tmpl_id'][0]  # Get the product template ID
        
            # Step 2: Query the product.template model using the product_template_id
            product_template_data = models.execute_kw(db, uid, password, 'product.template', 'search_read',
                                                    [[['id', '=', product_template_id]]],
                                                    {'fields': ['tec_fecha_edicion']})
        
            # If product template data exists and has a date, convert it to a datetime object
            if product_template_data and product_template_data[0].get('tec_fecha_edicion'):
                tec_fecha_edicion_str = product_template_data[0]['tec_fecha_edicion']
                tec_fecha_edicion = datetime.strptime(tec_fecha_edicion_str, '%Y-%m-%d').date()  # Convert to date object
                product['tec_fecha_edicion'] = tec_fecha_edicion  # Assign date to the product
            else:
                product['tec_fecha_edicion'] = None  # Handle missing date case
        else:
            product['tec_fecha_edicion'] = None  # Handle missing product template ID case
        
        Product_with_date.append(product)  # Add the product to the list

    # Find the product with the latest 'tec_fecha_edicion' (skip None values)
    Product_with_valid_date = [p for p in Product_with_date if p['tec_fecha_edicion'] is not None]

    if Product_with_valid_date:
        latest_product = max(Product_with_valid_date, key=lambda d: d['tec_fecha_edicion'])
        
        # Compare the latest 'tec_fecha_edicion' with today's date
        if latest_product['tec_fecha_edicion'] > date.today():
            preventa = True
            rawName = latest_product['name']
            rawFecha = latest_product['tec_fecha_edicion']
            nombreProducto = clean_product_name(rawName)
            fechaEdicion = rawFecha.strftime('%d/%m/%Y')
        else:
            preventa = False
    else:
        latest_product = None  # Handle case when no product has a valid date
        preventa = False

    # Determine the delivery status and prepare the response
    if RawStatus:
        DeliveryStatus = RawStatus[0]['state']
        if DeliveryStatus == 'assigned':
            estado = 'preparacion'
            informacion = {}
        elif DeliveryStatus == 'done':
            estado = 'enviado'
            ShippingInfo = models.execute_kw(db, uid, password, 'stock.picking', 'search_read',
                                             [[['origin', '=', order_name]]],
                                             {'fields': ['carrier_id', 'carrier_tracking_ref']})
            if ShippingInfo:
                informacion = {'carrier': ShippingInfo[0]['carrier_id'][1], 'tracking': ShippingInfo[0]['carrier_tracking_ref']}
            else:
                informacion = {}
        else:
            if preventa:
                estado = 'preventa'
                informacion = {'producto': nombreProducto, 'fecha': fechaEdicion}
            else:
                estado = 'esperando'
                informacion = {}
    else:
        estado = 'not_found'
        informacion = {}

    return jsonify({'estado': estado, 'informacion': informacion})

# Run the Flask server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))  # Render provides the port as an environment variable
    app.run(host='0.0.0.0', port=port)
