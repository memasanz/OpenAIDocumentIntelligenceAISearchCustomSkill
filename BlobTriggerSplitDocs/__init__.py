import logging
import os
import tempfile
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from PyPDF2 import PdfWriter, PdfReader
from azure.functions import InputStream
from dotenv import load_dotenv  

#Function Triggered when a new file lands in blog storage to create a pdf for the file.
def pdfsplit(source):
    logging.info('pdfsplit')
    try:
        logging.info('inside try')
        blob_files = []
        load_dotenv() 
        logging.info("about to get information from environment variables")
        source_container = os.environ['STORAGE_ACCOUNT_CONTAINER']
        dest_container = os.environ['STORAGE_ACCOUNT_CONTAINER_FOR_SPLITS'] 
        connection_string = os.environ['STORAGE_ACCOUNT_CONNECTION_STRING']

        
        logging.info('source_container = ' + source_container)

        file_name_without_extension = os.path.splitext(source)[0] 
        logging.info('file name without existing extension', file_name_without_extension)

        blob_service_client = BlobServiceClient.from_connection_string(connection_string)  
        container_client = blob_service_client.get_container_client(source_container)  
        # Download the PDF file locally  
        logging.info("downloading file")
        blob_client = container_client.get_blob_client(source)  
        logging.info('about to download blob file')
        with open(os.path.join(tempfile.gettempdir(), source), "wb") as my_blob: 
            logging.info('downloading blob file') 
            download_stream = blob_client.download_blob()  
            my_blob.write(download_stream.readall()) 

        with open(os.path.join(tempfile.gettempdir(), source), "rb") as input_pdf:  
            pdf_reader = PdfReader(input_pdf)  
            num_pages = len(pdf_reader.pages)
            logging.info(f"Number of pages in the pdf: {num_pages}")
            for page_num in range(num_pages):  
                pdf_writer = PdfWriter()  
                pdf_writer.add_page(pdf_reader.pages[page_num])  
                with open(os.path.join(tempfile.gettempdir(), f"{file_name_without_extension}_{page_num}.pdf"), "wb") as output_pdf:  
                    pdf_writer.write(output_pdf)  
                # Upload split up file to blob storage container  
                container_client2 = blob_service_client.get_container_client(dest_container)  
                blob_client2 = container_client2.get_blob_client(f"{file_name_without_extension}/{file_name_without_extension}_{page_num}.pdf")  
                blob_files.append(f"{file_name_without_extension}/{file_name_without_extension}_{page_num}.pdf")
                if blob_client2.exists():  
                    # Delete the existing blob  
                    container_client2.delete_blob(f"{file_name_without_extension}/{file_name_without_extension}_{page_num}.pdf")  
                with open(os.path.join(tempfile.gettempdir(), f"{file_name_without_extension}_{page_num}.pdf"), "rb") as data:  
                    logging.info(f"Uploading {file_name_without_extension}_{page_num}.pdf")
                    blob_client2.upload_blob(data) 
    except Exception as e:
        logging.error('Exception splitting pdf' + str(e))

def main(myblob: InputStream):
    logging.info(f"Python blob trigger function processed blob \n"
                 f"Name: {myblob.name}\n"
                 f"Blob Size: {myblob.length} bytes")
    pdfsplit(myblob.name)
