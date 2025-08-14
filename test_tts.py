import requests

response = requests.post("http://127.0.0.1:5000/generate-audio", json={
    "text": "The 2010 world cup was held in South Africa"
})

print("Status Code:", response.status_code)
print("Response:", response.json())