import json
import boto3
import os
from datetime import datetime

s3_client = boto3.client('s3',region_name='ap-south-1',config=boto3.session.Config(signature_version='s3v4')) 
BUCKET_NAME = 'medical-health-assistant-roselin'  

def lambda_handler(event, context):
    try:
        # 1. Parse incoming request
        body = json.loads(event['body'])
        patient_id = body.get('patient_id')
        file_name = body.get('file_name')

        if not patient_id:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'patient_id is required'})
            }

        if not file_name:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'file_name is required'})
            }
       
        # 2. Build S3 key (folder path)
        today = datetime.now().strftime('%Y-%m-%d')  
        s3_key = f"patients/{patient_id}/{today}/{file_name}"  
        
        # 3. Generate presigned URL
        presigned_url = s3_client.generate_presigned_url(
            'put_object', 
            Params={
                'Bucket': BUCKET_NAME,
                'Key': s3_key,
                'ContentType': 'application/pdf' 
            },
            ExpiresIn=300  
        )
       
        # 4. Return response
        return {
            'statusCode': 200, 
            'body': json.dumps({
                'upload_url': presigned_url, 
                's3_key': s3_key
            })
        }
       
    except Exception as e:
        return {
            'statusCode': 500, 
            'body': json.dumps({'error': str(e)})
        }