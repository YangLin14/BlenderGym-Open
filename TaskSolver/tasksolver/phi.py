import argparse
import os
# os.environ["CUDA_VISIBLE_DEVICES"] = "0" 

import json
import random
import re
import torch
import numpy as np
from tqdm import tqdm
import shortuuid
import sys


from torch.utils.data import Dataset, DataLoader
import requests 
from transformers import AutoModelForCausalLM 
from transformers import AutoProcessor 
from PIL import Image
import math

from .gpt4v import TaskSpec, ParsedAnswer, Question
from .exceptions import GPTOutputParseException, GPTMaxTriesExceededException
import threading
from typing import List, Tuple, Union
from loguru import logger
from copy import deepcopy
import time
import os

seed = 42
torch.manual_seed(seed)
np.random.seed(seed)
random.seed(seed)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False



class PhiModel(object):
    def __init__(self, task:TaskSpec,
                 model:str = "microsoft/Phi-3.5-vision-instruct"):

        self.task:TaskSpec = task
        self.model = self.get_model(model)

        self.processor = AutoProcessor.from_pretrained(model, 
            trust_remote_code=True, 
            num_crops=4
        ) 

    
    def get_model(self, model):
        # Load the open-source model in

        if model == 'microsoft/Phi-3.5-vision-instruct':
            model_weights = AutoModelForCausalLM.from_pretrained(
                model, 
                device_map="sequential", 
                trust_remote_code=True, 
                torch_dtype=torch.float16, 
                _attn_implementation='flash_attention_2'    
            )
            return model_weights

    def ask(self,  payload:dict, n_choices=1, temperature=0.7) -> Tuple[List[dict], List[dict]]:
        """
        args: 
            payload: json dictionary, prepared by `prepare_payload`
        """

        def phi_thread(self, idx, payload, results, temperature):

            # creation of payload
            mod_payload = deepcopy(payload)

            prompt = self.processor.tokenizer.apply_chat_template(
            payload['messages'], 
            tokenize=False, 
            add_generation_prompt=True
            )

            inputs = self.processor(prompt, mod_payload['images'], return_tensors="pt").to('cuda')

            generation_args = { 
                "max_new_tokens": payload['max_tokens'], 
                "temperature": temperature, 
                "do_sample": False, 
            } 

            try:
                # Preparation for inference
                generate_ids = self.model.generate(**inputs, 
                    eos_token_id=self.processor.tokenizer.eos_token_id, 
                    **generation_args
                )

                generate_ids = self.model.generate(**inputs, 
                    eos_token_id=self.processor.tokenizer.eos_token_id, 
                    **generation_args
                )

                # remove input tokens 
                generate_ids = generate_ids[:, inputs['input_ids'].shape[1]:]
                response = self.processor.batch_decode(generate_ids, 
                skip_special_tokens=True, 
                clean_up_tokenization_spaces=False)[0] 

            except Exception as e:
                raise e
            
            output_text = response
            print('outputs: ', output_text)
            message = {'content' : output_text}

            results[idx] = {"metadata": output_text, "message": message} 

            return

        assert n_choices >= 1
        results = [None]  * n_choices 
        if n_choices > 1:
            phi_jobs = [threading.Thread(target=phi_thread,
                            args=(self, idx, payload, results, temperature))
                                for idx in range(n_choices)]
            for job in phi_jobs:
                job.start()
            for job in phi_jobs:
                job.join()
        else:
            phi_thread(self, 0, payload, results, temperature)

        messages:List[dict] = [ res["message"] for res in results]
        metadata:List[dict] = [ res["metadata"] for res in results]
        return messages, metadata


    @staticmethod
    def prepare_payload(question:Question,
            max_tokens=1000,
            verbose:bool=False,
            prepend:Union[dict, None]=None,
            **kwargs
            ) -> dict:

        image_dic = None
        text = ''
        dic_list = question.get_json()
        for dic in question.get_json():
            # The case of text
            if dic['type'] == 'text':
                text += dic['text']

            # The case of vision input
            elif dic['type'] == 'image_url':
                image_dic = dic['image']

        if not image_dic:
            image_dic = Image.new('RGB', (512, 512), color = (255, 255, 255))

        messages = [
            {"role": "user", "content": text+"<|image_1|>\n"},
        ]

        payload = {
            "messages": messages,
            "images": [image_dic],
            "max_tokens": max_tokens,
        }


        return payload


    def rough_guess(self, question:Question, max_tokens=1000,
                    max_tries=10, query_id:int=0,
                    verbose=False, temperature=1,
                    **kwargs):
    
        p = self.prepare_payload(question, max_tokens = max_tokens, verbose=verbose, prepend=None, 
                                    model=self.model)

        ok = False
        reattempt = 0
        while not ok:
            response, meta_data = self.ask(p, temperature=temperature) 
            response = response[0] 
            logger.info(f'response: {response}')
            try: 
                parsed_response = self.task.answer_type.parser(response["content"])
            except GPTOutputParseException as e:
                logger.warning(f"The following was not parseable:\n\n{response}\n\nBecause\n\n{e}")

                # if not os.path.exists('errors/'):
                #     # Create the directory if it doesn't exist
                #     os.makedirs('errors/')
                # error_saved = f'errors/{time.strftime("%Y-%m-%d-%H-%M-%S")}.json'
                # with open(error_saved, "w")  as f:
                #     f.write(p_ans.code)
                # logger.warning(f"The following was not parseable. Saved in {error_saved}.")
                
                reattempt += 1
                if reattempt > max_tries:
                    logger.error(f"max tries ({max_tries}) exceeded.")
                    raise GPTMaxTriesExceededException
             
                logger.warning(f"Reattempt #{reattempt} querying LLM")
                continue
            ok = True 

        return parsed_response, response, meta_data, p

    def all_rough_guess(self, task, question:Question, max_tokens=1000,
                    max_tries=10, query_id:int=0,
                    verbose=False, temperature=1,
                    **kwargs):
    
        p = self.prepare_payload(question, max_tokens = max_tokens, verbose=verbose, prepend=None, 
                                    model=self.model)

        ok = False
        reattempt = 0
        while not ok:
            response, meta_data = self.ask(p, temperature=temperature) 
            response = response[0] 
            logger.info(f'response: {response}')
            try: 
                parsed_response = task.answer_type.parser(response["content"])
            except GPTOutputParseException as e:
                logger.warning(f"The following was not parseable:\n\n{response}\n\nBecause\n\n{e}")

                # if not os.path.exists('errors/'):
                #     # Create the directory if it doesn't exist
                #     os.makedirs('errors/')
                # error_saved = f'errors/{time.strftime("%Y-%m-%d-%H-%M-%S")}.json'
                # with open(error_saved, "w")  as f:
                #     f.write(p_ans.code)
                # logger.warning(f"The following was not parseable. Saved in {error_saved}.")
                
                reattempt += 1
                if reattempt > max_tries:
                    logger.error(f"max tries ({max_tries}) exceeded.")
                    raise GPTMaxTriesExceededException
             
                logger.warning(f"Reattempt #{reattempt} querying LLM")
                continue
            ok = True 

        return parsed_response, response, meta_data, p

    def many_rough_guesses(self, num_threads:int,
                           question:Question, max_tokens=1000,
                           verbose=False, max_tries=10, temperature=1
                           ) -> List[Tuple[ParsedAnswer, str, dict, dict]]:
        """
        Args:
            num_threads : number of independent threads.
            all other  arguments are same as those of `rough_guess()`

        Returns
            List of elements, each element is a tuple following the
            return signature of `rough_guess()`
        """

        p = self.prepare_payload(question, max_tokens = max_tokens, verbose=verbose, prepend=None, 
                                    model=self.model)

        #  TODO
        n_choices = num_threads

        # TODO: wrap in robust-ask method, repeatedly asks until parseable output. 
        ok = False
        reattempt = 0
        while not ok:
            response, meta_data = self.ask(p, n_choices=n_choices, temperature=temperature)
            try:
                parsed_response = [self.task.answer_type.parser(r["content"]) for r in response]
            except GPTOutputParseException as e:
                logger.warning(f"The following was not parseable:\n\n{response}\n\nBecause\n\n{e}")

                # TODO provide the parse error message into GPT for the next round to be parsable
                reattempt += 1
                if reattempt > max_tries:
                    logger.error(f"max tries ({max_tries}) exceeded.")
                    raise GPTMaxTriesExceededException
             
                logger.warning(f"Reattempt #{reattempt} querying LLM")
                continue
            ok = True 

        return parsed_response, response, meta_data, p

    def run_once(self, question:Question, max_tokens=1000, temperature=1, **kwargs):
        q = self.task.first_question(question) 
        p_ans, ans, meta, p = self.rough_guess(q, max_tokens=max_tokens, temperature=temperature, **kwargs)
        return p_ans, ans, meta, p





