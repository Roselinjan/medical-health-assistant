import json
import boto3
import uuid
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('medical-patients') 

def handle_get(event):
    email = event['queryStringParameters']['email']  
    
    response = table.get_item(
        Key={'email': email}
    )
    
    if 'Item' in response: 
        patient_id = response['Item']['patient_id']
        return {
            'statusCode': 200,
            'body': json.dumps({'patient_id': patient_id})
        }
    else:
        return {
            'statusCode': 404, 
            'body': json.dumps({'error': 'Patient not found'})
        }

def handle_post(event):
    body = json.loads(event['body'])  
    email = body['email']
    
    # check if patient already exists
    response = table.get_item(Key={'email': email})
    
    if 'Item' in response:
        # already exists — just return it, don't create a duplicate
        return {
            'statusCode': 200,
            'body': json.dumps({'patient_id': response['Item']['patient_id']})
        }
    
    # doesn't exist — create new patient
    new_patient_id = f"PAT-{uuid.uuid4().hex[:6].upper()}"
    
    table.put_item(Item={
        'email': email,
        'patient_id': new_patient_id,
        'created_at': datetime.utcnow().isoformat()  
    })
    
    return {
        'statusCode': 201,
        'body': json.dumps({'patient_id': new_patient_id})
    }

def lambda_handler(event, context):
    http_method = event['httpMethod']  
    
    if http_method == 'GET':
        return handle_get(event)
    elif http_method == 'POST':  
        return handle_post(event)
    else:
        return {
            'statusCode': 405,
            'body': json.dumps({'error': 'Method not allowed'})
        }