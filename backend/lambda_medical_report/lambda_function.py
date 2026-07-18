import json
import boto3
import urllib.parse
import re
import time
from datetime import datetime
from decimal import Decimal
import uuid

s3_client = boto3.client('s3')
textract_client = boto3.client('textract')
bedrock_client = boto3.client('bedrock-runtime', region_name='ap-south-1')
dynamodb = boto3.resource('dynamodb')  
table = dynamodb.Table('medical-reports')  
bedrock_agent_client = boto3.client('bedrock-agent-runtime',region_name='ap-south-1')
bedrock_agent_mgmt_client = boto3.client('bedrock-agent', region_name='ap-south-1')
cloudwatch = boto3.client('cloudwatch', region_name='ap-south-1')


INFERENCE_PROFILE_ID = 'apac.amazon.nova-pro-v1:0'
KB_ID = 'LBWMRHXRZM'
DS_ID = 'RZLSNAPFY6'

NORMAL_RANGES = {
    'hemoglobin': {'min': 12.6, 'max': 17.5, 'unit': 'g/dL', 'display_name': 'Hemoglobin'},
    'wbc': {'min': 4000, 'max': 11000, 'unit': '/cmm', 'display_name': 'WBC Count'},
    'mch': {'min': 27.0,'max': 32.0,'unit': 'pg','display_name': 'MCH'},
    'platelet': {'min': 150000, 'max': 400000, 'unit': '/cmm', 'display_name': 'Platelet Count'},
    'rbc': {'min': 3.5, 'max': 5.5, 'unit': 'million/cmm', 'display_name': 'RBC Count'},
    'mcv': {'min': 80, 'max': 100, 'unit': 'fL', 'display_name': 'MCV'},
    'mchc': {'min': 31.5, 'max': 36.0, 'unit': 'g/dL', 'display_name': 'MCHC'},
    'fasting blood sugar': {'min': 70, 'max': 100, 'unit': 'mg/dL', 'display_name': 'Fasting Blood Sugar'},
    'blood sugar pp': {'min': 70, 'max': 140, 'unit': 'mg/dL', 'display_name': 'Post Prandial Blood Sugar'},
    'hba1c': {'min': 4.0, 'max': 5.7, 'unit': '%', 'display_name': 'HbA1c'},
    'sgpt': {'min': 7, 'max': 56, 'unit': 'U/L', 'display_name': 'SGPT (Liver)'},
    'sgot': {'min': 10, 'max': 40, 'unit': 'U/L', 'display_name': 'SGOT (Liver)'},
    'creatinine': {'min': 0.6, 'max': 1.5, 'unit': 'mg/dL', 'display_name': 'Creatinine (Kidney)'},
    'tsh': {'min': 0.4, 'max': 4.0, 'unit': 'mIU/L', 'display_name': 'TSH (Thyroid)'},
    'eosinophils': {'min': 0, 'max': 6, 'unit': '%', 'display_name': 'Eosinophils'}
}

def normalize_platelet(value):
    # if value looks like it's already in thousands (100-500 range)
    if 100 <= value <= 500:
        return value * 1000  # convert to raw number
    # if value is already raw (100000-500000 range)
    return value


def decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

def convert_floats(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, list):
        return [convert_floats(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_floats(v) for k, v in obj.items()}
    return obj


def save_to_dynamodb(patient_id, report_date, s3_key, 
                     summary, critical_values, extracted_text):
    table.put_item(
        Item={
            'patient_id': patient_id,
            'report_date': report_date,
            's3_key': s3_key,
            'summary': summary,
            'critical_values': convert_floats(critical_values),
            'extracted_text': extracted_text,
            'created_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S") 
            
        }
    )
    print(f"Saved to DynamoDB: {patient_id} / {report_date}")

def check_existing_report(patient_id, report_date):
    response = table.get_item(
        Key={
            'patient_id': patient_id,  
            'report_date': report_date   
        }
    )
    return response.get('Item', None)  




def extract_with_textract(bucket, key):
    # start async job
    response = textract_client.start_document_text_detection(
        DocumentLocation={
            'S3Object': {
                'Bucket': bucket,
                'Name': key
            }
        }
    )
    job_id = response['JobId']
    print(f"Textract job started: {job_id}")

    # poll until complete
    while True:
        result = textract_client.get_document_text_detection(JobId=job_id)
        status = result['JobStatus']
        print(f"Textract status: {status}")
        if status == 'SUCCEEDED':
            break
        elif status == 'FAILED':
            raise Exception("Textract job failed")
        time.sleep(3)

    # extract all text
    extracted_text = ''
    for block in result['Blocks']:
        if block['BlockType'] == 'LINE':
            extracted_text += block['Text'] + '\n'

    # handle pagination
    while 'NextToken' in result:
        result = textract_client.get_document_text_detection(
            JobId=job_id,
            NextToken=result['NextToken']
        )
        for block in result['Blocks']:
            if block['BlockType'] == 'LINE':
                extracted_text += block['Text'] + '\n'

    print(f"Textract extracted {len(extracted_text)} characters")
    return extracted_text


def summarize_with_bedrock(extracted_text, patient_id, critical_values):
    lines = []
    for item in critical_values:
        line = f"{item['parameter']}: {item['value']} {item['unit']} ({item['status']}, normal range {item['min']}-{item['max']})"
        lines.append(line)

    if not lines:
        critical_values_text = "No critical values detected — all values are within normal range."
    else:
        critical_values_text = "\n".join(lines)

    prompt = f"""You are a caring medical assistant helping patients
                understand their medical reports.

                Patient ID: {patient_id}

                Medical Report:
                {extracted_text}

                The following values have ALREADY been determined to be abnormal (do not re-evaluate or second-guess these — treat them as confirmed facts):
                {critical_values_text}

                Please provide:
                1. A brief 2-line overview
                2. Key findings in simple bullet points
                3. Prefix ONLY the values listed above with ⚠️ — do not mark any other value as abnormal yourself
                4. End with: "Please consult your doctor for personalized advice"

                Maximum 300 words. Use simple English, avoid medical jargon.
                Do not start with phrases like "Sure" or "Here's".
                Do not use markdown headers like ###.
                Use plain bullet points only."""

    response = bedrock_client.converse(
        modelId=INFERENCE_PROFILE_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1000, "temperature": 0.3}
    )
    summary = response['output']['message']['content'][0]['text']
    print(f"Summary generated successfully")
    return summary

def extract_critical_values(extracted_text):
    critical_values = []
    text_lower = extracted_text.lower()
    for param, ranges in NORMAL_RANGES.items():
        pattern = rf'{param}[^\d]{{0,50}}(\d+\.?\d*)'
        match = re.search(pattern, text_lower)
        if match:
            value = float(match.group(1))
            if param == 'platelet':
                value = normalize_platelet(value)
    

            if value < ranges['min'] or value > ranges['max']:
                critical_values.append({
                    'parameter': ranges['display_name'],
                    'value': value,
                    'unit': ranges['unit'],
                    'min': ranges['min'],
                    'max': ranges['max'],
                    'status': 'LOW' if value < ranges['min'] else 'HIGH'
                })
    return critical_values


def publish_metric(metric_name, value=1, unit='Count'):
    cloudwatch.put_metric_data(
        Namespace='MedicalHealthAssistant',
        MetricData=[{
            'MetricName': metric_name,
            'Value': value,
            'Unit': unit
        }]
    )

def lambda_handler(event, context):
    if 'Records' not in event:
        return handle_summary_request(event)
    try:
        # 1. Extract bucket and key from S3 event
        bucket = event['Records'][0]['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(
            event['Records'][0]['s3']['object']['key']
        )

        # 2. Extract patient_id from key
        patient_id = key.split('/')[1]
        report_date = key.split('/')[2]
        filename = key.split('/')[3]
        sort_key = f"{report_date}#{filename}"

        # check cache first ──
        existing = check_existing_report(patient_id, sort_key)
        if existing:
            print(f"Cache hit — returning saved summary")
            publish_metric('CacheHits')
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Retrieved from cache',
                    'patient_id': patient_id,
                    'key': key,
                    'summary': existing['summary'],
                    'critical_values': existing['critical_values']
                }, default=decimal_to_float)
            }

        # 3. Log
        print(f"Processing file: {key}")
        print(f"Patient ID: {patient_id}")

        # 4. Extract text using Textract
        extracted_text = extract_with_textract(bucket, key)
        print(f"First 200 chars: {extracted_text[:200]}")

        # save extracted text to S3 for Knowledge Base indexing
        txt_key = key.replace('.pdf', '.txt')
        s3_client.put_object(
            Bucket=bucket,
            Key=txt_key,
            Body=extracted_text.encode('utf-8'),
            ContentType='text/plain'
        )
        print(f"Saved extracted text: {txt_key}")



        # save metadata file for KB patient_id filtering
        metadata_key = txt_key + '.metadata.json'
        metadata = {
            "metadataAttributes": {
                "patient_id": patient_id,
                "report_date": sort_key,
                "document_id": sort_key
            }
        }
        s3_client.put_object(
            Bucket=bucket,
            Key=metadata_key,
            Body=json.dumps(metadata).encode('utf-8'),
            ContentType='application/json'
        )
        print(f"Saved metadata: {metadata_key}")

        # 6. Extract critical values

        critical_values = extract_critical_values(extracted_text)
        print(f"Critical values found: {len(critical_values)}")

        # 5. Summarize with Bedrock

        summary = summarize_with_bedrock(extracted_text, patient_id, critical_values)

        save_to_dynamodb(
            patient_id=patient_id,
            report_date=sort_key,
            s3_key=key,
            summary=summary,
            critical_values=critical_values,
            extracted_text=extracted_text
            
        )
        publish_metric('PDFsProcessed') 
        # Trigger KB sync so the chatbot can immediately query this new report
        try:
            bedrock_agent_mgmt_client.start_ingestion_job(
                knowledgeBaseId=KB_ID,
                dataSourceId=DS_ID
            )
            print("KB sync triggered")
        except Exception as sync_error:
            print(f"KB sync trigger failed: {str(sync_error)}")



        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'File processed successfully',
                'patient_id': patient_id,
                'key': key,
                'summary': summary,
                'critical_values': critical_values
                    
        }, default=decimal_to_float)}

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
    
    
def handle_summary_request(event):
        """
        Handles GET /summary?patient_id=...&report_date=...
        Reads directly from DynamoDB and returns the stored report.
        """
        try:
            params = event.get('queryStringParameters') or {}
            patient_id = params.get('patient_id')
            report_date = params.get('report_date')

            if not patient_id or not report_date:
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': 'Missing patient_id or report_date'})
                }

            existing = check_existing_report(patient_id, report_date)

            if not existing:
                return {
                    'statusCode': 404,
                    'body': json.dumps({'message': 'Report not ready yet'})
                }

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'patient_id': patient_id,
                    'summary': existing['summary'],
                    'critical_values': existing['critical_values']
                }, default=decimal_to_float)
            }

        except Exception as e:
            print(f"Error in handle_summary_request: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': str(e)})
            }
