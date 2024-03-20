from azure.core.exceptions import ResourceNotFoundError
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents import SearchClient 
import azure.search.documents as azsearch  
import azure.functions as func
from langchain.embeddings import OpenAIEmbeddings
from langchain.text_splitter import TokenTextSplitter
from langchain.text_splitter import RecursiveCharacterTextSplitter
import logging
import json
import os
import openai
from OutputTables import OutputTables, OutputTable, TableCell
import pandas as pd
import re
import base64  
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from PyPDF2 import PdfWriter, PdfReader
import tempfile
import tiktoken
import time
from dotenv import load_dotenv  



def num_tokens_from_string(string: str) -> int:
    encoding = tiktoken.get_encoding("cl100k_base")
    num_tokens = len(encoding.encode(string))
    return num_tokens

def normalize_text1(s, sep_token = " \n "):
    s = re.sub(r'\s+',  ' ', s).strip()
    s = re.sub(r". ,","",s)
    s = s.replace("..",".")
    s = s.replace(". .",".")
    s = s.strip()
    return s

def normalize_text2(s, sep_token = " \n "):
    s = s.replace("\n", "")
    return s

def get_tables(result):
    try:
        myOutputTables = OutputTables()
        for i in range (0, len(result.tables)):
            table = result.tables[i]
            for j in range(0, len(table.bounding_regions)):
                region = table.bounding_regions[j]
                output_table = OutputTable(region.page_number, table.row_count, table.column_count)
                for c in range(0, len(table.cells)):
                    cell = table.cells[c]
                    output_cell = TableCell(cell.row_index, cell.column_index, cell.content, cell.row_span, cell.column_span)
                    output_table.add_record(output_cell)
                myOutputTables.add_table(output_table)
    except Exception as e:
        logging.info('exception in get tables method. ' +   str(e))
        myOutputTables = OutputTables()
    return myOutputTables

def get_text(page_number, result):
    try:
        page = result.pages[page_number];
        content = ' '
        for line_idx, line in enumerate(page.lines):
            content = content + line.content + '/n'
    except Exception as e:
        logging.info('exception in get text method. ' +   str(e))
        content = ' '
    return content

def get_tables_by_page(Outputtables, page_number):  
    filtered_tables = []  
    for table in Outputtables.tables:  
        if table.page_number == page_number:  
            filtered_tables.append(table)  
    return filtered_tables

def get_client():
    endpoint = os.getenv("OPENAI_API_BASE")
    api_key = os.getenv("OPENAI_API_KEY")
    openai_type = os.getenv("OPENAI_API_TYPE", None)
    api_version = os.getenv("OPENAI_API_VERSION", None)
 
    if openai_type=='azure':
        client = openai.AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version
        )
        return client
    else:
        openai.api_key = api_key
        return None
    
def get_embedding(text):
    text = text.replace("\n", " ")
    model = os.getenv('TEXT_EMBEDDING_MODEL')
    client = get_client()
    try:
        embeddings = client.embeddings.create(input = [text], model=model).data[0].embedding
        
    except Exception as e:
        logging.info(e)
        time.sleep(5)
        try:
            logging.info('retrying to get embeddings after a 5 second pause')
            embeddings = client.embeddings.create(input = [text], model=model).data[0].embedding
        except Exception as e:
            logging.info(e)
            embeddings = []

    return embeddings



def getfilesforsource(source):
    #logging.info('getfilesforsource')
    blob_files = []
    source_container = os.environ['STORAGE_ACCOUNT_CONTAINER'] 
    dest_container = os.environ['STORAGE_ACCOUNT_CONTAINER_FOR_SPLITS'] 
    connection_string = os.environ['STORAGE_ACCOUNT_CONNECTION_STRING']
    file_name_without_extension = os.path.splitext(source)[0] 
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)  
        container_client = blob_service_client.get_container_client(dest_container)  
        blobs = container_client.list_blobs(name_starts_with=file_name_without_extension + "/")  

        # Print the names of the blobs 
        blob_files = [] 
        for blob in blobs:  
            print(blob.name)  
            blob_files.append(blob.name)
    
        return blob_files
    
    except Exception as e:

        logging.error('Error getting files for source' + str(e))
        raise Exception("Error message" + e.message)  

    

def text_split_embedd(source):
    logging.info('text_split_embedd')

    endpoint = os.environ["FORMS_RECOGNIZER_ENDPOINT"]
    key = os.environ['FORMS_RECOGNIZER_KEY']
    formUrl = "https://" + os.environ['STORAGE_ACCOUNT'] + ".blob.core.windows.net/" + os.environ['STORAGE_ACCOUNT_CONTAINER'] + "/" + source
    formUrl = formUrl.replace(' ', '%20')
    logging.info("document = " + formUrl)
    #be sure to give document intelligence access to file storage.
    document_analysis_client = DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = document_analysis_client.begin_analyze_document_from_url("prebuilt-layout", formUrl)
    result = poller.result()

    logging.info('result.pages = ' + str(len(result.pages)))

    myOutputTables = get_tables(result)
    page_content = []
    embeddings = [[]]* len(result.pages)
    data = [''] * len(result.pages)


    for i in range(0, len(result.pages)):
        logging.info('text_split_embedd for source:' + source + ' in for loop, i = ' + str(i) + '/' + str(len(result.pages)))
        content = get_text(i, result)
        print('content type = ' + str(type(content)))
        page_outputtables = get_tables_by_page(myOutputTables, i+1)
        for j in range(0, len(page_outputtables)):
            content = '\n' + '\n'  + content + page_outputtables[j].to_markdown()

        if content == None:
            logging.info('content is None') 
            content = ' '

        content_to_vectorize = content

        token_count = num_tokens_from_string(content_to_vectorize) 
        if token_count > 8192:
            logging.info("trimming content removing punctuation.")
            content_to_vectorize = normalize_text1(content_to_vectorize)
            token_count = num_tokens_from_string(content_to_vectorize) 
            logging.info('token count after initial cleaning: ' + str(token_count))
            if token_count >= 8192:
                logging.info("trimming content removing newlines.")
                content_to_vectorize = normalize_text2(content_to_vectorize)
                token_count = num_tokens_from_string(content_to_vectorize) 
                logging.info('token count after second cleaning: ' + str(token_count))
                if token_count > 8192:
                    logging.info("trimming content to fit into context of a page.")
                    text_splitter = TokenTextSplitter(chunk_size=8191, chunk_overlap=0)
                    texts = text_splitter.split_text(content_to_vectorize)
                    content_to_vectorize = texts[0]
                    token_count = num_tokens_from_string(content_to_vectorize) 
                    logging.info('token count after truncating: ' + str(token_count))

        logging.info('about to set data element ' + str(i) + '/' + str(len(embeddings)))
        data[i] = content #actual content

        if content is None or content == '':
            embeddings[i] = []
        try:
            embeddings[i] = get_embedding(content_to_vectorize)
        except Exception as e:
            logging.warning('Error getting embedding' + e.message)
            embeddings[i] = []
            continue
    return data, embeddings



def push_to_vector_index(data, embeddings, source):
    logging.info('push_to_vector_index')
    try:
        search_keys = []
        service_endpoint = os.environ['COG_SEARCH_ENDPOINT']
        index_name = os.environ['COG_SEARCH_INDEX_NAME']
        key = os.environ['COG_SEARCH_KEY']
        credential = AzureKeyCredential(key)
        title_embeddings = get_embedding(source)
        path = "https://" + os.environ['STORAGE_ACCOUNT'] + ".blob.core.windows.net/" + os.environ['STORAGE_ACCOUNT_CONTAINER'] + "/" + source
        path = path.replace(' ', '%20')


        search_client = SearchClient(endpoint=service_endpoint, index_name=index_name, credential=credential)
        docs = search_client.search(search_text=f"{source}", search_fields=["title"], include_total_count = True)
        count = docs.get_count()

        search_client = SearchClient(service_endpoint, index_name, AzureKeyCredential(key))
        docs = search_client.search(search_text=f"{source}", search_fields=["title"], include_total_count = True)
        count = docs.get_count()

        delete_docs = []
        if count > 0:
            for x in docs:
                if x['path'] == path:
                    delete_docs.append({"key" : x['key']})
            if len(delete_docs) > 0:
                logging.info('about to delete documents')
                logging.info('delete_docs:' + str(len(delete_docs)))
                result = search_client.delete_documents(documents=delete_docs)
                for i in range (0, len(result)):
                    if result[0].succeeded  == False:
                        raise ValueError('A very specific bad thing happened.')
                logging.info('deletion occured:'  + str(len(result)))
        else:
            logging.info('no documents to delete')

        logging.info('about to upload documents')

    except Exception as e:
        logging.info(e)
        logging.info('Error in search_client.search')
        pass


    try:
        logging.info(str(len(data)))
    
    except Exception as e:
        logging.info(e)
        logging.info('Error in len(data)')
        pass

    #source
    file_name_without_extension = os.path.splitext(source)[0] 
    for i in range(len(data)):
        logging.info('push to vector index, i = ' + str(i))

        logging.info((len(data)))
        logging.info((len(embeddings)))
                     
        pdf_path = "https://" + os.environ['STORAGE_ACCOUNT'] + ".blob.core.windows.net/" + os.environ['STORAGE_ACCOUNT_CONTAINER_FOR_SPLITS'] + "/" + file_name_without_extension + "/" + file_name_without_extension + "_" + str(i + 1) + '.pdf'
        pdf_path = pdf_path.replace(' ', '%20')
    
        text = data[i]
        title_embeddings = title_embeddings
        embedd = embeddings[i]

        random_str = source + "_" + str(i + 1)
        random_str = re.sub(r'[\[\]\(\)\*\&\^\%\$\#\@\!\.]', '-', random_str)
        random_str = random_str.replace(" ", "-")
        random_str = random_str.replace(",", "-")
        search_keys.append(str(random_str))

        
        document = {
            "key": f"{random_str}",
            "index": f"{i + 1}",
            "title": f"{source}",
            "content": f"{text}",
            "path": f"{path}",
            "contentVector": embedd,
            "titleVector": title_embeddings, 
            "pathChunkJPG": pdf_path,
            "pageNo": f"{i + 1}",
            "pathChunkPDF": pdf_path
        }

        result = search_client.upload_documents(documents=document)
        json_string = json.dumps(document)
    return search_keys

def compose_response(json_data):
    logging.info('in compose response')
    body  = json.loads(json_data)
    assert ('values' in body), "request does not implement the custom skill interface"
    values = body['values']
    # Prepare the Output before the loop
    results = {}
    results["values"] = []

    endpoint = os.environ["FORMS_RECOGNIZER_ENDPOINT"]
    logging.info(endpoint)
    key = os.environ["FORMS_RECOGNIZER_KEY"]
    logging.info(key)
    for value in values:
        output_record = transform_value(value)
        if output_record != None:
            results["values"].append(output_record)
            break
    return json.dumps(results, ensure_ascii=False)

def transform_value(value):
    logging.info('in transform_value')
    try:
        recordId = value['recordId']
    except AssertionError  as error:
        return None

    # Validate the inputs
    try:  
        logging.info(value)       
        assert ('data' in value), "'data' field is required."
        data = value['data']   
    except AssertionError  as error:
        return (
            {
            "recordId": recordId,
            "data":{},
            "errors": [ { "message": "Error:" + error.args[0] }   ]
            })
    try:             
        source = value['data']['source']
        # API_BASE = os.environ["OPENAI_API_BASE"]
        # API_KEY = os.environ["OPENAI_API_KEY"]
        # API_VERSION = os.environ["OPENAI_API_VERSION"]
        # API_TYPE = os.environ["OPENAI_API_TYPE"]
        

        # openai.api_type = API_TYPE
        # openai.api_base = API_BASE
        # openai.api_version = API_VERSION
        # openai.api_key  = API_KEY

        data, embeddings = text_split_embedd(source)
        vector_search_keys = push_to_vector_index(data, embeddings, source)


    except Exception as e:
        logging.info(e)
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "Could not complete operation for record."  } , {e}  ]
            })

    return ({
            "recordId": recordId,
            "data": {
                "embeddings_text": data,
                "embeddings": embeddings,
                "vector_search_keys": vector_search_keys
                    }
            })

def main(req: func.HttpRequest) -> func.HttpResponse:

    try:
        body = json.dumps(req.get_json())
        if body:
            result = compose_response(body)
            return func.HttpResponse(result, mimetype="application/json")
        else:
            return func.HttpResponse(
                "The body of the request could not be parsed",
                status_code=400
            )
    except ValueError:
        return func.HttpResponse(
             "The body of the request could not be parsed",
             status_code=400
        )
    except KeyError:
        return func.HttpResponse(   
             "Skill configuration error. Endpoint, key and model_id required.",
             status_code=400
        )
    except AssertionError  as error:
        return func.HttpResponse(   
             "Request format is not a valid custom skill input",
             status_code=400
        )