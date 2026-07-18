import json
import boto3
from datetime import datetime
from boto3.dynamodb.conditions import Key

bedrock_agent_client = boto3.client('bedrock-agent-runtime', region_name='ap-south-1')
bedrock_client = boto3.client('bedrock-runtime', region_name='ap-south-1')
dynamodb = boto3.resource('dynamodb')
reports_table = dynamodb.Table('medical-reports')
chat_history_table = dynamodb.Table('medical-chat-history')

KB_ID = 'LBWMRHXRZM'
MODEL_ARN = 'arn:aws:bedrock:ap-south-1:847399098942:inference-profile/apac.amazon.nova-pro-v1:0'
INFERENCE_PROFILE_ID = 'apac.amazon.nova-pro-v1:0'

GUARDRAIL_ID = 'ub2ptchl94pd'
GUARDRAIL_VERSION = '8'

INSUFFICIENT_PHRASES = ["cannot find", "insufficient information", "do not provide", "unable to assist"]

SYNTHESIS_INDICATORS = [
    'everything abnormal', 'all abnormal', 'list abnormal', 'what is abnormal',
    'critical values', 'all critical', 'everything critical', 'what all',
    'list everything', 'summarize abnormal', 'all my abnormal'
]

from datetime import datetime

def save_chat_message(patient_id, document_id, question, answer):
    timestamp = datetime.utcnow().isoformat()
    chat_history_table.put_item(
        Item={
            'patient_id': patient_id,
            'sort_key': f"{document_id}#{timestamp}",
            'question': question,
            'answer': answer,
            'timestamp': timestamp
        }
    )

def get_chat_history(patient_id, document_id, limit=6):
    response = chat_history_table.query(
        KeyConditionExpression=Key('patient_id').eq(patient_id) & Key('sort_key').begins_with(document_id),
        ScanIndexForward=False,
        Limit=limit
    )
    items = response.get('Items', [])
    items.reverse()
    return items

def format_history_for_prompt(history_items):
    if not history_items:
        return ""
    lines = []
    for item in history_items:
        lines.append(f"Patient asked: {item['question']}")
        lines.append(f"You answered: {item['answer']}")
    return "\n".join(lines)

def is_multi_value_synthesis_question(question):
    q_lower = question.lower()
    return any(phrase in q_lower for phrase in SYNTHESIS_INDICATORS)

def get_stored_critical_values(patient_id, document_id):
    response = reports_table.get_item(
        Key={
            'patient_id': patient_id,
            'report_date': document_id   # document_id == sort_key == report_date#filename
        }
    )
    item = response.get('Item')
    if not item:
        return None
    return item.get('critical_values', [])


def format_critical_values_answer(critical_values):
    if not critical_values:
        return "Based on your report, all your values are within the normal range. Please consult your doctor for personalized advice."

    lines = ["Based on your uploaded report, here are the abnormal values:\n"]
    for item in critical_values:
        lines.append(
            f"⚠️ {item['parameter']}: {item['value']} {item['unit']} "
            f"({item['status']}, normal range {item['min']}-{item['max']})"
        )
    lines.append("\n Please consult your doctor for personalized advice.")
    return "\n".join(lines)


def is_health_related(question):
    prompt = f"""Question: "{question}"

Is this question related to health, medical topics, lab reports, blood tests, symptoms, or the human body?
Answer with only one word: YES or NO."""

    response = bedrock_client.converse(
        modelId=INFERENCE_PROFILE_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 5, "temperature": 0}
    )
    answer = response['output']['message']['content'][0]['text'].strip().upper()
    return answer.startswith("YES")

def extract_sources(kb_response):
    citations = kb_response.get('citations', [])
    sources = set()
    
    for citation in citations:
        for ref in citation.get('retrievedReferences', []):
            text = ref.get('content', {}).get('text', '')
            if text:
                sources.add(text)
    
    return list(sources)


def has_sufficient_context(kb_response):
    answer_text = kb_response['output']['text'].lower()
    text_suggests_no_info = any(phrase in answer_text for phrase in INSUFFICIENT_PHRASES)
    citations = kb_response.get('citations', [])
    has_real_citations = any(len(c["retrievedReferences"]) > 0 for c in citations)
    return not text_suggests_no_info and has_real_citations

def is_general_guidance_question(question):
    prompt = f"""
            Question: "{question}"

            Is the user asking for:
            - general health guidance,
            - lifestyle advice,
            - diet or exercise advice,
            - prevention,
            - ways to improve a condition,
            OR
            - the general meaning, definition, or explanation of a medical term or lab parameter, without asking for their own report value?

            Answer only YES or NO.
            """

    response = bedrock_client.converse(
        modelId=INFERENCE_PROFILE_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 5, "temperature": 0}
    )

    answer = response['output']['message']['content'][0]['text'].strip().upper()
    return answer.startswith("YES")

def fallback_missing_report(question, kb_result):
    prompt = f"""
You are a medical education assistant.

The patient's uploaded medical report has already been searched.

Patient Question:
"{question}"

Knowledge Base Response:
"{kb_result}"

Instructions:

- The requested information was not found in the uploaded medical report.
- Start your answer with:
"This information is not available in your uploaded medical report."

- Then say:
"The following is general medical information."

- Provide a brief, simple explanation that answers the patient's question.
- Do NOT recommend medications, drug names, dosages, or make a diagnosis.
- Never invent laboratory values or claim the report contains information that wasn't found.
- Keep the answer under 150 words.
- End with:
"Please consult your doctor for personalized advice."
"""

    response = bedrock_client.converse(
        modelId=INFERENCE_PROFILE_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={
            "maxTokens": 400,
            "temperature": 0.3
        },
        guardrailConfig={
            "guardrailIdentifier": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION
        }
    )

    return response['output']['message']['content'][0]['text']

FOLLOWUP_INDICATORS = ['those', 'them', 'that', 'it', 'these', 'this one', 'explain', 'why', 'more detail']

def is_likely_followup(question):
    q_lower = question.lower()
    return any(word in q_lower for word in FOLLOWUP_INDICATORS) and len(question.split()) < 8

def rewrite_followup_question(question, history_items):
    if not history_items:
        return question

    recent_history = history_items[-1:]  # last 1-2 exchanges is usually enough
    history_text = "\n".join(
        f"Patient asked: {item['question']}\nAnswer: {item['answer']}"
        for item in recent_history
    )

    prompt = f"""Previous conversation:
{history_text}

Follow-up question: "{question}"


Rewrite the follow-up question as a single, short, standalone question that includes the specific value or topic being referred to. Keep it focused on ONE topic only — do not combine multiple values from the conversation. Only output the rewritten question, nothing else."""
    response = bedrock_client.converse(
        modelId=INFERENCE_PROFILE_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 100, "temperature": 0}
    )
    rewritten = response['output']['message']['content'][0]['text'].strip()
    return rewritten

def fallback_general_education(question):
    prompt = f"""
You are a medical education assistant.

The patient asked:

"{question}"

Instructions:
- Answer the question as general medical education.
- Explain the concept in simple language.
- Do NOT mention the uploaded medical report.
- Do NOT say the information is missing from the report.
- Do NOT mention whether the report contains this value.
- Do NOT recommend medications, drug names, dosages, or make a diagnosis.
- Keep the answer under 150 words.
- End with:
"Please consult your doctor for personalized advice."
"""
   
    response = bedrock_client.converse(
        modelId=INFERENCE_PROFILE_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 400, "temperature": 0.3},
        guardrailConfig={
            "guardrailIdentifier": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION
        }
    )
    return response['output']['message']['content'][0]['text']


def lambda_handler(event, context):
    try:
        body = json.loads(event['body'])
        question = body['question']
        patient_id = body['patient_id']
        document_id = body['document_id']
        session_id = body.get('session_id')
        print(f"DEBUG - received session_id: {session_id}, is truthy: {bool(session_id)}")
        history_items = get_chat_history(patient_id, document_id)
        history_context = format_history_for_prompt(history_items)
        print(f"DEBUG - history_items count: {len(history_items)}")
        print(f"DEBUG - history_context: {history_context}")
        print(f"DEBUG - patient_id: {patient_id}, document_id: {document_id}")
        if session_id and is_likely_followup(question) and history_items:
            question = rewrite_followup_question(question, history_items)
            print(f"DEBUG - rewritten question: {question}")

        # Reject non-health questions unless they are follow-up questions
        if not is_health_related(question) and not (
            session_id and is_likely_followup(question)
        ):
            return {
                'statusCode': 200,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({
                    'answer': "I'm designed to answer questions only about your uploaded medical report. Feel free to ask me about your test results, values, or what they mean!"
                })
            }
        
        if is_multi_value_synthesis_question(question):
            critical_values = get_stored_critical_values(patient_id, document_id)
            if critical_values is not None:
                answer = format_critical_values_answer(critical_values)
                print(f"DEBUG - Critical values answer: {answer}")
                print(f"DEBUG - about to save question: {question}")
                save_chat_message(patient_id, document_id, question, answer)
                return {
                    'statusCode': 200,
                    'headers': {'Access-Control-Allow-Origin': '*'},
                    'body': json.dumps({
                        'answer': answer,
                        'sources': [],
                        'session_id': session_id
                    })
                }


        kb_params = {
            'input': {
                'text': question
            },
            'retrieveAndGenerateConfiguration': {
                'type': 'KNOWLEDGE_BASE',
                'knowledgeBaseConfiguration': {
                    'knowledgeBaseId': KB_ID,
                    'modelArn': MODEL_ARN,
                    'generationConfiguration': {
                        'guardrailConfiguration': {
                            'guardrailId': GUARDRAIL_ID,
                            'guardrailVersion': GUARDRAIL_VERSION
                        }
                    },
                    'retrievalConfiguration': {
                        'vectorSearchConfiguration': {
                            'filter': {
                                'andAll': [
                                    {
                                        'equals': {
                                            'key': 'patient_id',
                                            'value': patient_id
                                        }
                                    },
                                    {
                                        'equals': {
                                            'key': 'document_id',
                                            'value': document_id
                                        }
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }

        # Continue previous conversation if session exists
        if session_id:
            kb_params['sessionId'] = session_id

        kb_response = bedrock_agent_client.retrieve_and_generate(**kb_params)

        print(json.dumps(kb_response, indent=2, default=str))

        if has_sufficient_context(kb_response):
            answer = kb_response['output']['text']
            sources = extract_sources(kb_response)
        else:
            result = is_general_guidance_question(question)
            print(f"GENERAL_GUIDANCE = {result}")

            if result:
                answer = fallback_general_education(question)
            else:
                answer = fallback_missing_report(
                    question,
                    kb_response['output']['text']
                )

            sources = []
        
        print(f"DEBUG - about to save question: {question}")
        save_chat_message(patient_id, document_id, question, answer)


        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'answer': answer,
                'sources': sources,
                'session_id': kb_response.get('sessionId')
            })
        }

    except Exception as e:
        print(str(e))
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }