import copy
import time
from typing import Union, Callable, List, Tuple, Dict
import tqdm
from llama_cpp import Llama

from src.utils import ExtendCompletionLength

__all__ = [
    "ArithmeticProbOrdHider",
]

class ArithmeticProbOrdHider:
    
    def __init__(self, llm: Llama, bits_per_token: int = 3, skip_tokens: int = 0, disable_tqdm: bool = True):
        self.llm = llm
        self.forbidden_tokens = [llm.tokenize(bytes(token, encoding="utf-8"))[-1] for token in ["\n", "...", "…"]]
        self.forbidden_tokens += [llm.token_eos(), llm.token_bos()]
        self.end_of_sequence_tokens = [".", "!", "?", "\n"]
        self.repeat_penalty = 1.5
        self.get_valid_tokens = self.initialize_token_getter(self.llm, bits_per_token)
        self.update_feed = self.initialize_feed_updater(self.llm, skip_tokens)
        self.disable_tqdm = disable_tqdm
        self.bits_per_token = bits_per_token

    @staticmethod
    def _token_is_usable(token: str, other_tokens: List[str]) -> bool:
        if token in [" "]: # blocks too many tokens
            return False
        for other in other_tokens:
            if (token in other) or (other in token):
                return False
        return True

    def initialize_feed_updater(self, llm: Llama, skip_tokens: int = 0) -> Callable:
        logits_processor = ExtendCompletionLength(min_completion_length=skip_tokens, eos_token_id=self.forbidden_tokens)
        def _update_feed(feed: str, next_token: str, return_padding: bool = False) -> str:
            if skip_tokens == 0:
                return feed + next_token
            updated_feed = feed + next_token
            logits_processor.update_prompt_length(llm.tokenize(bytes(updated_feed, "utf-8")))
            start = time.time()
            feed_padding = llm(updated_feed, top_p=0, max_tokens=skip_tokens, logprobs=1, temperature= 0, top_k=1, repeat_penalty=self.repeat_penalty, logits_processor=logits_processor)["choices"][0]["text"]
            print(f"UPDATE: Time to generate {skip_tokens} tokens: {time.time() - start}")
            if return_padding:
                return updated_feed + feed_padding, feed_padding
            return updated_feed + feed_padding
        
        return _update_feed

    def initialize_token_getter(self, llm: Llama, bits_per_token: int = 3) -> Callable[[str, bool, int], List[str]]:
        logits_processor = ExtendCompletionLength(min_completion_length=1, eos_token_id=self.forbidden_tokens)
        def _get_valid_token(prompt: str, get_end_condition: bool = False, recursive_extra_tokens: int = 0) -> List[str]:
            nr_tokens_to_generate = 2**(bits_per_token + recursive_extra_tokens)
            logits_processor.update_prompt_length(llm.tokenize(bytes(prompt, "utf-8")))
            start = time.time()
            output = llm(prompt, top_p=0, max_tokens=1, logprobs=nr_tokens_to_generate, temperature= 0, top_k=1, repeat_penalty=self.repeat_penalty, logits_processor=logits_processor)["choices"][0]
            print(f"GET TOKEN: Time to generate {nr_tokens_to_generate} tokens: {time.time() - start}")
            finish_reason = output["finish_reason"]
            if finish_reason == "stop":
                return ""
            next_token_probs = list(output["logprobs"]["top_logprobs"][0])
            return_tokens = []
            for token in next_token_probs:
                if ArithmeticProbOrdHider._token_is_usable(token, return_tokens):
                    return_tokens.append(token)
                
                if len(return_tokens) == (2**(bits_per_token) + get_end_condition):
                    break
            else:
                return _get_valid_token(prompt, get_end_condition, recursive_extra_tokens + 1)[:2**bits_per_token]
            
            if get_end_condition:
                return return_tokens[2**(bits_per_token)]
            return return_tokens
        
        return _get_valid_token
    
    @staticmethod
    def text_to_extend_n_chars_with(txt: str, n: int) -> str:
        what_we_have = txt[-n:]
        from_a_point = what_we_have[what_we_have.find(".")+1:]
        return from_a_point

    @staticmethod
    def pad_with_0(x: str, bits_per_token: int) -> str:
        return "0"*(bits_per_token - (len(x) % bits_per_token)) + x if len(x) % bits_per_token else x 
    
    @staticmethod
    def split_in_separators(txt, seps = None):
        if seps is None:
            seps = [" ", ",", ":", ";", ".", "-", "_", "/", "\\"]
        default_sep = seps[0]

        # we skip seps[0] because that's the default separator
        for sep in seps[1:]:
            txt = txt.replace(sep, default_sep)
        return [i.strip() for i in txt.split(default_sep) if len(i) > 0]

    def hide_in_single_article(self, binary_secrets: List[str], prompt: str, soft_max_chars_limit: int = 450) -> Tuple[str, str]:
        """
        Encode as many bits of the binary secret into news_string.

        Args:
            prompt (str): prompt words to use for the next encoding
            binary_secret (str): string secret in binary representation
            soft_max_chars_limit (int, optional): _description_. Defaults to 450.

        Returns:
            tuple[str, str]: [news_string containing, secret and all bits that did not fit]
        """
        # Reset LLM cache
        self.llm.reset()
        # Create stego feed from real feed for prompt
        doctored_article: str = prompt
        for j, binary_secret in tqdm.tqdm(enumerate(binary_secrets), "Hidding message nr: ", disable=self.disable_tqdm):
            # Iterate until message is contained in article
            for i, next_bits in tqdm.tqdm([(_i, binary_secret[_i:_i+self.bits_per_token]) for _i in range(0, len(binary_secret), self.bits_per_token)], "Message hidden: ", disable=self.disable_tqdm):
                next_token_probs = self.get_valid_tokens(doctored_article)
                chosen_ind = int(next_bits, 2)
                next_token = next_token_probs[chosen_ind]
                doctored_article, text_padding = self.update_feed(doctored_article, next_token, return_padding=True)
                if len(doctored_article) > soft_max_chars_limit and any([_terminator_token in text_padding for _terminator_token in self.end_of_sequence_tokens]):  # doctored_article.endswith((".", "!", "?")):
                    _found_token = [_terminator_token for i, _terminator_token in enumerate(self.end_of_sequence_tokens) if _terminator_token in text_padding][0]
                    index_in_padding_from_end = len(text_padding) - text_padding.find(_found_token)
                    return doctored_article[:-index_in_padding_from_end], [binary_secret[i+self.bits_per_token:], *binary_secrets[j+1:]]

            # Append one "out-of-range" token to signalize the change from one secret to the next
            doctored_article = self.update_feed(doctored_article, self.get_valid_tokens(doctored_article, get_end_condition=True))
        
        # Once all secrets are hidden, append another "out-of-range" token to signalize ALL secrets are hidden
        doctored_article = self.update_feed(doctored_article, self.get_valid_tokens(doctored_article, get_end_condition=True))
        return doctored_article, []
    
    def hide_in_whole_newsfeed(self, news_feed: List[str], binary_secrets: List[str], soft_max_chars_lim: int = 450, nr_prompt_words: int = 5) -> Dict[str, List[str]]:
        """
        Encodes the binary_secret in the newsfeed.

        Args:
            newsfeed (list[str]): A list of different newsfeeds as strings
            binary_secret (str): string secret in binary representation
            soft_max_chars_lim (int, optional): _description_. Defaults to 450.
            nr_prompt_words (int, optional): how many words of the next prompt are used to encode. Defaults to 5.

        Returns:
            str: newsfeed with encoded secret concatenated with the rest of newsfeed, which was not used for encoding.
        """
        
        doctored_newsfeed = []
        remaining_secrets = binary_secrets
        for news_article in tqdm.tqdm(news_feed, "Hidding messages is article nr: ", disable=self.disable_tqdm):
            prompt = " ".join(self.split_in_separators(news_article)[:nr_prompt_words])
            doctored_article, remaining_secrets = self.hide_in_single_article(remaining_secrets, prompt, soft_max_chars_lim)
            if soft_max_chars_lim > len(doctored_article):
                len_diff = len(news_article) - len(doctored_article)
                doctored_article += self.text_to_extend_n_chars_with(news_article, len_diff)

            doctored_newsfeed.append(doctored_article)
            if len(remaining_secrets) == 0:
                break
        return {"feed": doctored_newsfeed + news_feed[len(doctored_newsfeed):]}

    @staticmethod
    def get_next_possible_token(next_token_probs: List[str], news_article: int, i: int) -> Union[str, bool]:
            max_len_next_token = max([len(token) for token in next_token_probs]) # Check the max length to check in message where the next token has to be found
            next_possible_tokens = [token for token in next_token_probs if news_article[i:i+max_len_next_token].startswith(token)] # If there's still more than one that would fit
            next_possible_tokens = sorted(next_possible_tokens, key= lambda x: news_article[i:i+max_len_next_token].find(x)) # Keep the one that occurs first -> next token
            if len(next_possible_tokens) > 0:
                return next_possible_tokens[0]
            return False

    def retrieve_single_secret_from_single_article(self, news_article: str, analyzed_feed: str) -> Tuple[str, bool]:
        decoded_msg = ""
        all_messages_found = False

        # Iterate until news feed is exhausted
        pbar = tqdm.tqdm(total=len(news_article), desc="Article parsed looking for secret: ", disable=self.disable_tqdm)
        while (i := len(analyzed_feed)) < len(news_article):
            next_token_probs = self.get_valid_tokens(analyzed_feed)
            next_token = self.get_next_possible_token(next_token_probs, news_article, i)
            if next_token is False:
                """
                If the next token does not belong to the top 2**bits_per_token
                then we assume the whole message has been decoded.
                """
                temp_feed = copy.deepcopy(analyzed_feed)
                temp_feed = self.update_feed(temp_feed, self.get_valid_tokens(temp_feed, True))
                next_token_probs = self.get_valid_tokens(temp_feed)
                if len(news_article) > len(temp_feed):
                    next_token = self.get_next_possible_token(next_token_probs, news_article, len(temp_feed))
                    all_messages_found = not next_token
                else:
                    all_messages_found = True
                    temp_feed = news_article
                return decoded_msg, all_messages_found, temp_feed
                    
            chosen_ind = next_token_probs.index(next_token)
            analyzed_feed = self.update_feed(analyzed_feed, next_token)
            decoded_msg += self.pad_with_0(bin(chosen_ind)[2:], self.bits_per_token)
            pbar.n = i
            pbar.refresh()

        return decoded_msg, all_messages_found, ""


    def retrieve_multiple_secrets_from_single_article(self, news_article: str, nr_prompt_words: int = 5) -> Tuple[str, bool]:
        analyzed_feed = " ".join(self.split_in_separators(news_article)[:nr_prompt_words])
        decoded_messages = []
        all_messages_found = False
        while True:
            decoded_msg, all_messages_found, analyzed_feed = self.retrieve_single_secret_from_single_article(news_article, analyzed_feed)
            decoded_messages.append(decoded_msg)
            if all_messages_found:
                return decoded_messages, [True for _ in range(len(decoded_messages))]
            elif analyzed_feed == "":
                return decoded_messages, [i != len(decoded_messages) - 1 for i in range(len(decoded_messages))]
        
    @staticmethod
    def concat_decoded_secrets(decoded_secrets: List[str], completely_decoded_flags: List[bool]) -> List[str]:
        out_stuff = []
        next_false = 0
        next_true = 0
        while True:
            try:
                next_false = completely_decoded_flags.index(False)
                next_true = completely_decoded_flags.index(True)+1
            except ValueError:
                out_stuff.extend(decoded_secrets)
                break
            out_stuff.append("".join(decoded_secrets[next_false:next_true]))
            completely_decoded_flags = completely_decoded_flags[next_true:]
            decoded_secrets = decoded_secrets[next_true:]
        return out_stuff
        


    
    def retrieve_multiple_secrets_from_news_feed(self, news_feed: List[str], nr_prompt_words: int = 5) -> List[str]:
        decoded_secrets = []
        fully_decoded_flags = []
        for news_article in tqdm.tqdm(news_feed, "Retrieving secret from article nr: ", disable=self.disable_tqdm):
            decoded_msgs, completely_decoded_flags = self.retrieve_multiple_secrets_from_single_article(news_article, nr_prompt_words)
            decoded_secrets.extend(decoded_msgs)
            fully_decoded_flags.extend(completely_decoded_flags)
            if all(completely_decoded_flags):
                break
        
        decoded_secrets = self.concat_decoded_secrets(decoded_secrets, fully_decoded_flags)
        return decoded_secrets