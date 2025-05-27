def analyze_numbers():

    numbers = list(map(float, input('Enter numbers separated by space: ').split()))
    average = sum(numbers) / len(numbers)
    for num in numbers:
        if num > average:
            print(f'{num} is above average')
        elif num < average:
            print(f'{num} is below average')
        else:
            print(f'{num} is equal to the average')
if __name__ == '__main__':
    analyze_numbers()